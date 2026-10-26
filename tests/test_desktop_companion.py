import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from desktop_bridge.bridge import (
    BridgeError, BridgeWorker, CDPClient, COOKIE_URLS, SessionUploader,
    cookie_header, normalize_server_url, trusted_page_url, validate_debugger_url,
)


def cookies(identity="123", token="key_123"):
    return [
        {"name": "unb", "value": identity, "domain": ".goofish.com", "expires": -1},
        {"name": "_m_h5_tk", "value": token, "domain": ".goofish.com", "expires": -1},
    ]


class CompanionBoundaryTests(unittest.TestCase):
    def test_server_requires_secure_remote_origin_and_clean_url(self):
        self.assertEqual(normalize_server_url(" https://example.com/butler/ "), "https://example.com/butler")
        self.assertEqual(normalize_server_url("http://127.0.0.1:38888"), "http://127.0.0.1:38888")
        self.assertEqual(normalize_server_url("http://[::1]:38888"), "http://[::1]:38888")
        for value in (
            "http://example.com", "https://user:secret@example.com", "https://example.com/?token=secret",
            "https://example.com/#fragment", "file:///tmp/config", "https://example.com:bad",
            "https://example.com/\nsecret", "http://127.0.0.1.evil.test", "https://example.com\\evil",
        ):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                normalize_server_url(value)

    def test_debug_connection_stays_on_selected_loopback_port(self):
        self.assertEqual(validate_debugger_url("ws://localhost:9222/devtools/page/abc", 9222),
                         "ws://127.0.0.1:9222/devtools/page/abc")
        for value in (
            "ws://remote.example:9222/devtools/page/abc", "ws://127.0.0.1:9000/devtools/page/abc",
            "ws://127.0.0.1.evil.test:9222/devtools/page/abc", "ws://user@127.0.0.1:9222/devtools/page/abc",
            "ws://127.0.0.1:9222/devtools/browser/abc", "wss://127.0.0.1:9222/devtools/page/abc",
            "ws://127.0.0.1:9222/devtools/page/abc?secret=1",
        ):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                validate_debugger_url(value, 9222)
        self.assertTrue(trusted_page_url("https://seller.goofish.com/"))
        for value in ("http://seller.goofish.com", "https://goofish.com.evil.test", "https://evilgoofish.com", "https://user@goofish.com"):
            self.assertFalse(trusted_page_url(value))

    def test_cookie_filter_drops_other_sites_expired_and_header_injection(self):
        values = cookies() + [
            {"name": "other_auth", "value": "outside", "domain": ".taobao.com"},
            {"name": "fake", "value": "outside", "domain": ".goofish.com.evil.test"},
            {"name": "expired", "value": "old", "domain": ".goofish.com", "expires": 1},
            {"name": "injection", "value": "x; bad=y", "domain": ".goofish.com"},
            {"name": "header", "value": "x\r\nOther: secret", "domain": ".goofish.com"},
            {"name": "http_only", "value": "included", "domain": ".goofish.com", "httpOnly": True},
        ]
        value = cookie_header(values, now=10)
        self.assertEqual(value, "_m_h5_tk=key_123; http_only=included; unb=123")
        self.assertEqual(value, cookie_header(list(reversed(values)), now=10))
        self.assertIsNone(cookie_header(cookies()[:1]))

    def test_ambiguous_sessions_are_not_flattened(self):
        with self.assertRaises(BridgeError):
            cookie_header(cookies() + [{"name": "unb", "value": "999", "domain": "seller.goofish.com"}])

    def test_unfinished_challenge_waits_for_manual_completion(self):
        values = cookies() + [{"name": "x5secdata", "value": "challenge", "domain": ".goofish.com"}]
        self.assertIsNone(cookie_header(values))
        self.assertIsNotNone(cookie_header(values + [{"name": "x5sec", "value": "complete", "domain": ".goofish.com"}]))
        cdp = CDPClient()
        cdp._targets = Mock(return_value=[{"type": "page", "url": "https://seller.goofish.com/punish"}])
        cdp._read_target = Mock()
        with self.assertRaisesRegex(BridgeError, "手动完成"):
            cdp.read_cookie()
        cdp._read_target.assert_not_called()

    def test_cdp_reads_only_explicit_urls_and_never_sends_site_headers(self):
        websocket = Mock()
        websocket.recv.return_value = json.dumps({"id": 1, "result": {"cookies": cookies()}})
        context = Mock()
        context.__enter__ = Mock(return_value=websocket)
        context.__exit__ = Mock(return_value=False)
        cdp = CDPClient()
        cdp._targets = Mock(return_value=[
            {"type": "page", "url": "https://unrelated.example", "webSocketDebuggerUrl": "ws://remote:9222/devtools/page/evil"},
            {"type": "page", "url": "https://seller.goofish.com", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ok"},
        ])
        with patch("desktop_bridge.bridge.connect", return_value=context) as connect:
            self.assertIn("unb=123", cdp.read_cookie())
        self.assertEqual(connect.call_count, 1)
        command = json.loads(websocket.send.call_args.args[0])
        self.assertEqual(command["method"], "Network.getCookies")
        self.assertEqual(command["params"]["urls"], COOKIE_URLS)
        self.assertNotIn("additional_headers", connect.call_args.kwargs)

    def test_multiple_signed_in_accounts_require_user_selection(self):
        cdp = CDPClient()
        cdp._targets = Mock(return_value=[{"type": "page", "url": "https://seller.goofish.com"}] * 2)
        cdp._read_target = Mock(side_effect=[cookie_header(cookies("123")), cookie_header(cookies("456"))])
        with self.assertRaisesRegex(BridgeError, "多个闲鱼账号"):
            cdp.read_cookie()


class CompanionSyncTests(unittest.TestCase):
    def setUp(self):
        self.cdp = Mock()
        self.cdp.read_cookie.return_value = "unb=123; _m_h5_tk=one"
        self.uploader = Mock()
        self.uploader.upload.return_value = {"success": True, "changed": True}
        self.status = Mock()
        self.worker = BridgeWorker(self.cdp, self.uploader, self.status)

    def test_stable_change_upload_and_heartbeat_do_not_flood_server(self):
        self.worker.step(now=0)
        self.uploader.upload.assert_not_called()
        self.worker.step(now=3)
        self.assertEqual(self.uploader.upload.call_count, 1)
        self.worker.step(now=6)
        self.assertEqual(self.uploader.upload.call_count, 1)
        self.worker.step(now=63)
        self.assertEqual(self.uploader.upload.call_count, 2)
        self.cdp.read_cookie.return_value = "unb=123; _m_h5_tk=two"
        self.worker.step(now=66)
        self.worker.step(now=69)
        self.assertEqual(self.uploader.upload.call_count, 2)
        self.worker.step(now=78)
        self.assertEqual(self.uploader.upload.call_count, 3)
        self.assertEqual(self.uploader.upload.call_args.args[0], "unb=123; _m_h5_tk=two")

    def test_transient_failure_retries_same_cookie_and_honors_retry_after(self):
        self.uploader.upload.side_effect = [BridgeError("稍后重试", retry_after=15), {"success": True, "changed": True}]
        self.worker.step(now=0)
        self.worker.step(now=3)
        self.worker.step(now=6)
        self.worker.step(now=17)
        self.assertEqual(self.uploader.upload.call_count, 1)
        self.worker.step(now=18)
        self.assertEqual(self.uploader.upload.call_count, 2)
        self.assertIsNotNone(self.worker.last_uploaded)

    def test_cookie_disappearing_requires_new_stable_samples(self):
        self.worker.step(now=0)
        self.cdp.read_cookie.return_value = None
        self.worker.step(now=3)
        self.cdp.read_cookie.return_value = "unb=123; _m_h5_tk=one"
        self.worker.step(now=6)
        self.uploader.upload.assert_not_called()
        self.worker.step(now=9)
        self.uploader.upload.assert_called_once()

    def test_client_disconnect_resets_stable_samples(self):
        self.worker.step(now=0)
        self.cdp.read_cookie.side_effect = BridgeError("等待客户端")
        self.worker.step(now=3)
        self.assertIsNone(self.worker.candidate)
        self.cdp.read_cookie.side_effect = None
        self.worker.step(now=6)
        self.uploader.upload.assert_not_called()
        self.worker.step(now=9)
        self.uploader.upload.assert_called_once()

    def test_auth_failure_stops_loop_and_releases_cookie(self):
        self.uploader.upload.side_effect = BridgeError("令牌已失效", fatal=True)
        event = Mock()
        event.is_set.return_value = False
        self.worker.run(event)
        self.assertEqual(self.uploader.upload.call_count, 1)
        self.assertIsNone(self.worker.candidate)
        self.assertIsNone(self.worker.last_uploaded)
        self.assertIn("令牌已失效", self.status.call_args.args[0])


class CompanionUploadTests(unittest.TestCase):
    def test_http_errors_do_not_echo_credential_payloads(self):
        for status, fatal in ((401, True), (403, True), (429, False), (422, False), (503, False)):
            uploader = SessionUploader("https://butler.example", "secret-token")
            uploader.opener = Mock()
            uploader.opener.open.side_effect = HTTPError(uploader.endpoint, status, "secret-cookie", {"Retry-After": "22"}, io.BytesIO(b"secret-cookie"))
            with self.subTest(status=status), self.assertRaises(BridgeError) as error:
                uploader.upload("secret-cookie")
            self.assertEqual(error.exception.fatal, fatal)
            self.assertNotIn("secret", str(error.exception))
            if status == 429:
                self.assertEqual(error.exception.retry_after, 22)

    def test_redirect_never_forwards_pairing_token(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(307)
                self.send_header("Location", "/stolen")
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            uploader = SessionUploader(f"http://127.0.0.1:{server.server_port}", "scoped-token")
            with self.assertRaisesRegex(BridgeError, "跳转"):
                uploader.upload("unb=123; _m_h5_tk=value")
            self.assertEqual(requests, [("/desktop-bridge/session", "Bearer scoped-token")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
