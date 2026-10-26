"""Read a local seller-client session and sync it to its paired butler account.

This module never persists or logs credentials and does not complete challenges.
The selected client must expose a Chromium DevTools endpoint on loopback.
"""
from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from websockets.sync.client import connect

try:
    from .queries import SDK_PROBE, query_expression
except ImportError:
    from queries import SDK_PROBE, query_expression


COOKIE_URLS = [
    "https://seller.goofish.com/",
    "https://www.goofish.com/",
    "https://h5api.m.goofish.com/",
]
MAX_RESPONSE_BYTES = 1024 * 1024
COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class BridgeError(Exception):
    """A credential-free message safe to display in the connection window."""

    def __init__(self, message: str, retry_after: float = 0, fatal: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.fatal = fatal


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_port(port: int | str) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError):
        raise BridgeError("本机调试端口应为 1 到 65535 的整数。", fatal=True) from None
    if not 1 <= value <= 65535:
        raise BridgeError("本机调试端口应为 1 到 65535 的整数。", fatal=True)
    return value


def normalize_server_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise BridgeError("服务器地址格式不正确。", fatal=True) from None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 33 for char in value)
        or "\\" in value
    ):
        raise BridgeError("请填写不含用户名、查询参数或片段的服务器地址。", fatal=True)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback(parsed.hostname)):
        raise BridgeError("远程服务器须使用 HTTPS；SSH 隧道可使用 http://127.0.0.1:端口。", fatal=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json_response(response) -> dict | list:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BridgeError("连接返回的数据过大，请检查地址和客户端。")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise BridgeError("连接未返回有效数据，请检查地址和服务版本。") from None


def trusted_page_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and (host == "goofish.com" or host.endswith(".goofish.com"))
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except (ValueError, TypeError):
        return False


def challenge_page_url(value: str) -> bool:
    if not trusted_page_url(value):
        return False
    parsed = urlsplit(value)
    return any(marker in parsed.path.lower() for marker in ("punish", "validate", "captcha"))


def validate_debugger_url(value: str, port: int) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "ws"
            and is_loopback(parsed.hostname)
            and parsed.port == port
            and parsed.username is None
            and parsed.password is None
            and parsed.path.startswith("/devtools/page/")
            and not parsed.query
            and not parsed.fragment
            and not any(ord(char) < 33 for char in value)
            and "\\" not in value
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise BridgeError("客户端返回了非本机调试地址，已拒绝连接。")
    # Avoid resolving a returned localhost hostname through a proxy or DNS.
    if parsed.hostname == "localhost":
        return urlunsplit(("ws", f"127.0.0.1:{port}", parsed.path, "", ""))
    return value


def cookie_header(cookies: list[dict], now: float | None = None) -> str | None:
    """Keep only valid Goofish cookies returned for our explicit target URLs."""
    now = time.time() if now is None else now
    values: dict[str, str] = {}
    # Prefer the root-domain value for optional duplicate cookie names.
    ordered = sorted((c for c in cookies if isinstance(c, dict)), key=lambda c: len(str(c.get("domain", ""))), reverse=True)
    for cookie in ordered:
        domain = str(cookie.get("domain", "")).lower().lstrip(".")
        if domain != "goofish.com" and not domain.endswith(".goofish.com"):
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or not COOKIE_NAME.fullmatch(name):
            continue
        if any(ord(char) < 32 or ord(char) == 127 or char == ";" for char in value):
            continue
        expires = cookie.get("expires", -1)
        if isinstance(expires, (int, float)) and expires > 0 and expires <= now:
            continue
        if name in ("unb", "_m_h5_tk") and name in values and values[name] != value:
            raise BridgeError("客户端存在相互冲突的登录会话，请退出其他闲鱼账号后重试。")
        values[name] = value
    if not values.get("unb") or not values.get("_m_h5_tk"):
        return None
    if any(values.get(name) for name in ("x5secdata", "x5sectag", "x5step")) and not values.get("x5sec"):
        return None
    result = "; ".join(f"{name}={values[name]}" for name in sorted(values))
    if len(result) > 32000:
        raise BridgeError("当前会话内容过长，暂时不能同步。")
    return result


class CDPClient:
    def __init__(self, port: int = 9222):
        self.port = validate_port(port)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _targets(self) -> list[dict]:
        try:
            with self.opener.open(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as response:
                targets = read_json_response(response)
        except BridgeError:
            raise
        except (OSError, URLError, HTTPError):
            raise BridgeError(
                "尚未连接到客户端。请完全退出闲鱼卖家客服，再用“启动客户端并连接”重开；"
                "若仍失败，此版本可能不支持本机调试连接。"
            ) from None
        if not isinstance(targets, list):
            raise BridgeError("本机端口不是受支持的客户端调试服务。")
        return [target for target in targets if isinstance(target, dict)]

    def _read_target(self, target: dict) -> str | None:
        url = validate_debugger_url(target.get("webSocketDebuggerUrl", ""), self.port)
        try:
            with connect(url, open_timeout=5, close_timeout=1, max_size=MAX_RESPONSE_BYTES) as websocket:
                websocket.send(json.dumps({"id": 1, "method": "Network.getCookies", "params": {"urls": COOKIE_URLS}}))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    payload = json.loads(websocket.recv(timeout=max(0.01, deadline - time.monotonic())))
                    if not isinstance(payload, dict) or payload.get("id") != 1:
                        continue
                    if "error" in payload:
                        raise BridgeError("此客户端不支持会话读取，请检查客户端版本及登录页面。")
                    result = payload.get("result")
                    if not isinstance(result, dict) or not isinstance(result.get("cookies"), list):
                        raise BridgeError("客户端没有返回可识别的会话。")
                    return cookie_header(result["cookies"])
        except BridgeError:
            raise
        except Exception:
            # Exceptions from networking libraries can contain URLs or payloads.
            raise BridgeError("客户端连接已断开，正在等待重新连接。") from None
        raise BridgeError("读取客户端会话超时，正在重试。")

    def read_cookie(self) -> str | None:
        targets = [target for target in self._targets() if target.get("type") == "page" and trusted_page_url(target.get("url", ""))]
        if not targets:
            raise BridgeError("已连接调试端口，请在闲鱼卖家客服中打开闲鱼页面并登录、手动完成验证。")
        if any(challenge_page_url(target.get("url", "")) for target in targets):
            raise BridgeError("检测到平台验证页面，请在闲鱼卖家客服中手动完成验证后继续同步。")
        sessions: dict[str, str] = {}
        last_error: BridgeError | None = None
        for target in targets[:10]:
            try:
                value = self._read_target(target)
            except BridgeError as error:
                last_error = error
                continue
            if value:
                identity = next(part[4:] for part in value.split("; ") if part.startswith("unb="))
                sessions[identity] = value
        if len(sessions) > 1:
            raise BridgeError("检测到多个闲鱼账号，请只保留要同步的账号后重试。")
        if sessions:
            return next(iter(sessions.values()))
        if last_error:
            raise last_error
        return None

    def _evaluate_target(self, target: dict, expression: str, timeout=5):
        url = validate_debugger_url(target.get('webSocketDebuggerUrl', ''), self.port)
        try:
            with connect(url, open_timeout=5, close_timeout=1, max_size=MAX_RESPONSE_BYTES) as websocket:
                websocket.send(json.dumps({'id': 2, 'method': 'Runtime.evaluate', 'params': {
                    'expression': expression, 'awaitPromise': True, 'returnByValue': True,
                    'timeout': timeout * 1000,
                }}))
                deadline = time.monotonic() + timeout + 1
                while time.monotonic() < deadline:
                    payload = json.loads(websocket.recv(timeout=max(.01, deadline - time.monotonic())))
                    if not isinstance(payload, dict) or payload.get('id') != 2:
                        continue
                    result = payload.get('result', {})
                    if 'error' in payload or 'exceptionDetails' in result:
                        raise BridgeError('客户端页面查询接口不可用。')
                    return result.get('result', {}).get('value')
        except BridgeError:
            raise
        except Exception:
            raise BridgeError('客户端查询连接中断，请检查客户端页面。') from None
        raise BridgeError('客户端查询超时。')

    def _query_targets(self):
        targets = self._targets()
        if any(challenge_page_url(t.get('url', '')) for t in targets):
            raise BridgeError('请先在闲鱼卖家客服中手动完成验证。')
        return [t for t in targets if t.get('type') == 'page' and trusted_page_url(t.get('url', ''))
                and urlsplit(t['url']).hostname in ('seller.goofish.com', 'www.goofish.com')][:10]

    def query_sdk_available(self):
        try:
            for target in self._query_targets():
                try:
                    if self._evaluate_target(target, SDK_PROBE) is True:
                        return True
                except BridgeError:
                    continue
        except BridgeError:
            pass
        return False

    def execute_query(self, job):
        try:
            expression = query_expression(job)
            targets = self._query_targets()
            # Prefer the origin used by this API in the existing server client.
            seller_api = '.merchant.' in job['api']
            preferred = 'seller.goofish.com' if seller_api else 'www.goofish.com'
            targets.sort(key=lambda t: urlsplit(t['url']).hostname != preferred)
            for target in targets:
                if self._evaluate_target(target, SDK_PROBE) is not True:
                    continue
                def matches():
                    cookie = self._read_target(target)
                    return cookie and any(p == 'unb=' + job['account_unb'] for p in cookie.split('; '))
                if not matches():
                    return {'error': 'account_changed'}
                outcome = self._evaluate_target(target, expression, timeout=18)
                if not matches():
                    return {'error': 'account_changed'}
                if not isinstance(outcome, dict) or not ('result' in outcome or 'error' in outcome):
                    return {'error': 'request_failed'}
                return outcome
            return {'error': 'sdk_unavailable'}
        except (BridgeError, ValueError, KeyError, TypeError):
            return {'error': 'client_disconnected'}


class SessionUploader:
    def __init__(self, server_url: str, token: str):
        self.endpoint = normalize_server_url(server_url) + "/desktop-bridge/session"
        self.token = token.strip()
        if not self.token or any(ord(char) < 33 or ord(char) > 126 for char in self.token):
            raise BridgeError("请粘贴管家生成的专属连接令牌。", fatal=True)
        self.opener = build_opener(NoRedirect())

    def _post(self, endpoint: str, body: dict) -> dict:
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=True).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=12) as response:
                payload = read_json_response(response)
        except HTTPError as error:
            status = error.code
            retry_after = error.headers.get("Retry-After", "15") if error.headers else "15"
            error.close()
            if status == 401:
                raise BridgeError("连接令牌已失效、被撤销或过期，请在管家重新生成并连接。", fatal=True) from None
            if status == 403:
                raise BridgeError("当前登录账号与绑定账号不符，或账号权限已变更，请核对后重新连接。", fatal=True) from None
            if status == 429:
                try:
                    delay = min(300, max(3, float(retry_after)))
                except (ValueError, TypeError):
                    delay = 15
                raise BridgeError("服务器正在限制同步频率，稍后自动重试。", retry_after=delay) from None
            if 300 <= status < 400:
                raise BridgeError("服务器返回跳转，已停止上传。请填写最终 HTTPS 服务器地址。", fatal=True) from None
            if status in (400, 422):
                raise BridgeError("服务器未接受当前会话，请确认登录并完成平台验证。", retry_after=15) from None
            if status == 404:
                raise BridgeError("服务器尚未安装桌面接入接口，或地址不正确。", fatal=True) from None
            raise BridgeError("服务器暂时无法同步，稍后自动重试。") from None
        except BridgeError:
            raise
        except (OSError, URLError, ValueError):
            raise BridgeError("服务器连接失败，请检查服务器地址、证书和网络；稍后自动重试。") from None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise BridgeError("服务器未确认同步成功，稍后自动重试。")
        return payload

    def upload(self, cookie: str) -> dict:
        payload = self._post(self.endpoint, {'value': cookie})
        return {'success': True, 'changed': payload.get('changed') is True}

    def poll_query(self, identity: str, available: bool):
        return self._post(self.endpoint.removesuffix('/session') + '/queries/poll',
                          {'account_unb': identity, 'sdk_available': available})

    def complete_query(self, identity: str, job_id: str, outcome: dict):
        return self._post(self.endpoint.removesuffix('/session') + '/queries/result',
                          {'account_unb': identity, 'job_id': job_id, **outcome})


class BridgeWorker:
    """Two stable samples, change-only sync and a deduplicated 60s heartbeat."""

    def __init__(self, cdp: CDPClient, uploader: SessionUploader, on_status: Callable[[str], None]):
        self.cdp = cdp
        self.uploader = uploader
        self.on_status = on_status
        self.candidate: str | None = None
        self.stable_samples = 0
        self.last_uploaded: str | None = None
        self.last_upload_at: float | None = None
        self.next_attempt = 0.0
        self.failures = 0

    def step(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if now < self.next_attempt:
            return
        reading_client = True
        try:
            cookie = self.cdp.read_cookie()
            reading_client = False
            if not cookie:
                self.candidate = None
                self.stable_samples = 0
                self.on_status("等待登录会话；如出现滑块，请在闲鱼卖家客服中手动完成。")
                return
            if cookie != self.candidate:
                self.candidate = cookie
                self.stable_samples = 1
                self.on_status("已读取登录会话，等待会话稳定后自动同步。")
                return
            self.stable_samples += 1
            if self.stable_samples < 2:
                return
            elapsed = float("inf") if self.last_upload_at is None else now - self.last_upload_at
            if elapsed >= 15 and (cookie != self.last_uploaded or elapsed >= 60):
                result = self.uploader.upload(cookie)
                self.last_uploaded = cookie
                self.last_upload_at = now
                self.failures = 0
                self.on_status("会话已自动同步到服务器；后续变化会继续同步。" if result["changed"] else "已连接服务器，会话无需更新。")
            if self.last_uploaded is not None:
                identity = next(p[4:] for p in cookie.split('; ') if p.startswith('unb='))
                available = self.cdp.query_sdk_available()
                reply = self.uploader.poll_query(identity, available)
                if reply.get('query_enabled') is True:
                    self.on_status('桌面查询已就绪，等待商品或订单查询。' if available
                                   else '会话已连接，但未找到查询接口；请打开客户端商品或订单页面。')
                job = reply.get('job')
                if isinstance(job, dict):
                    if job.get('account_unb') != identity:
                        raise BridgeError('查询任务账号不匹配，已停止连接。', fatal=True)
                    outcome = self.cdp.execute_query(job)
                    self.uploader.complete_query(identity, job['id'], outcome)
                    self.on_status('客户端查询结果已返回服务器。' if 'result' in outcome
                                   else '客户端未完成查询，请检查商品或订单页面及验证提示。')
        except BridgeError as error:
            if reading_client:
                self.candidate = None
                self.stable_samples = 0
            self.on_status(str(error))
            if error.fatal:
                raise
            self.failures += 1
            self.next_attempt = now + max(error.retry_after, min(60, 3 * 2 ** min(self.failures - 1, 5)))

    def run(self, stop_event: Event) -> None:
        try:
            while not stop_event.is_set():
                self.step()
                stop_event.wait(3)
        except BridgeError:
            pass
        except Exception:
            self.on_status("连接工具遇到异常，请停止后重新连接。")
        finally:
            # Release session contents when the loop ends.
            self.candidate = self.last_uploaded = None


def launch_client(executable: str, port: int) -> subprocess.Popen:
    path = Path(executable).expanduser()
    if not path.is_file() or path.suffix.lower() != ".exe":
        raise BridgeError("请选择闲鱼卖家客服的 .exe 程序文件。", fatal=True)
    port = validate_port(port)
    try:
        return subprocess.Popen(
            [str(path), "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"],
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise BridgeError("无法启动所选客户端，请检查程序路径和本机运行权限。", fatal=True) from None
