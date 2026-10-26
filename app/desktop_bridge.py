"""Account-scoped desktop session synchronization; never stores raw pairing tokens."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, SecretStr

from utils.xianyu_utils import drop_stale_captcha_challenge
from desktop_bridge.protocol import ERRORS, MAX_RESULT_BYTES

TOKEN_LIFETIME = 90 * 24 * 60 * 60
MIN_SYNC_INTERVAL = 15
TOKEN_PATTERN = re.compile(r"db1_[A-Za-z0-9_-]{43}\Z")


def _cookie_fields(value: str) -> dict[str, str]:
    fields = {}
    for part in value.split(';'):
        part = part.strip()
        if not part:
            continue
        if '=' not in part:
            raise HTTPException(400, 'Cookie 格式不完整，请在客户端重新登录')
        key, content = part.split('=', 1)
        if not key or key in fields or any(char.isspace() for char in key):
            raise HTTPException(400, 'Cookie 存在重复或无效字段')
        fields[key] = content
    return fields


def _normalize_cookie(value: str) -> str:
    value = value.strip()
    if value.lower().startswith('cookie:'):
        value = value[7:].strip()
    if not value or len(value) > 32000 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HTTPException(400, 'Cookie 内容无效或过长')
    fields = _cookie_fields(value)
    if not fields.get('unb') or not fields.get('_m_h5_tk'):
        raise HTTPException(400, '会话缺少 unb 或 _m_h5_tk，请先在客户端完成登录')
    return drop_stale_captcha_challenge('; '.join(f'{key}={content}' for key, content in fields.items()))


def _fingerprint(value: str) -> str:
    fields = _cookie_fields(value)
    encoded = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None


class DesktopSessionRequest(BaseModel):
    # SecretStr keeps credentials out of repr/exception diagnostics. Size and format
    # checks run after parsing, so their error response doesn't echo the input.
    value: SecretStr


class DesktopQuerySettings(BaseModel):
    enabled: bool


class DesktopQueryPoll(BaseModel):
    account_unb: str
    sdk_available: bool


class DesktopQueryResult(BaseModel):
    account_unb: str
    job_id: str
    result: dict | None = None
    error: str | None = None


class DesktopBridgeService:
    def __init__(self, db_manager: Any, import_session: Callable, clock: Callable = time.time):
        self.db = db_manager
        self.import_session = import_session
        self.clock = clock
        # Serialize pairing/revocation with import, without holding the DB lock
        # while CookieManager waits for its asyncio thread (which also reads DB).
        self.lock = threading.RLock()

    def _owned_account(self, cid: str, user_id: int):
        row = self.db.conn.execute(
            'SELECT value FROM cookies WHERE id = ? AND user_id = ?', (cid, user_id)
        ).fetchone()
        if not row:
            raise HTTPException(403, '无权限操作该账号')
        return row[0]

    def status(self, cid: str, user_id: int) -> dict:
        with self.lock, self.db.lock:
            value = self._owned_account(cid, user_id)
            row = self.db.conn.execute('''
                SELECT user_id, account_unb, created_at, expires_at, last_seen_at, last_sync_at,
                       query_enabled, query_ready, last_query_seen_at
                FROM desktop_bridge_tokens WHERE cookie_id = ?
            ''', (cid,)).fetchone()
            if row:
                try:
                    identity_matches = _cookie_fields(value).get('unb') == row[1] and row[0] == user_id
                except HTTPException:
                    identity_matches = False
                if not identity_matches:
                    self.db.conn.execute('DELETE FROM desktop_bridge_tokens WHERE cookie_id = ?', (cid,))
                    self.db.conn.commit()
                    row = None
            return {
                'enabled': bool(row and row[3] > self.clock()),
                'query_enabled': bool(row and row[6]),
                'query_ready': bool(row and row[3] > self.clock() and row[7] and row[8] is not None
                                    and self.clock() - row[8] <= 45),
                'last_query_seen_at': _iso(row[8]) if row else None,
                **{key: _iso(row[index]) if row else None for key, index in (
                    ('created_at', 2), ('expires_at', 3), ('last_seen_at', 4), ('last_sync_at', 5)
                )},
            }

    def pair(self, cid: str, user_id: int) -> dict:
        with self.lock, self.db.lock:
            value = self._owned_account(cid, user_id)
            identity = _cookie_fields(value).get('unb')
            if not identity:
                raise HTTPException(400, '原账号缺少 unb，请先添加完整的账号 Cookie')
            token = 'db1_' + secrets.token_urlsafe(32)
            now = self.clock()
            expires = now + TOKEN_LIFETIME
            self.db.conn.execute('''
                INSERT OR REPLACE INTO desktop_bridge_tokens
                (cookie_id, user_id, account_unb, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (cid, user_id, identity, hashlib.sha256(token.encode()).hexdigest(), now, expires))
            self.db.conn.commit()
            return {'token': token, 'expires_at': _iso(expires)}

    def revoke(self, cid: str, user_id: int) -> dict:
        with self.lock, self.db.lock:
            self._owned_account(cid, user_id)
            self.db.conn.execute('DELETE FROM desktop_bridge_tokens WHERE cookie_id = ?', (cid,))
            self.db.conn.commit()
            return {'success': True}

    def _authenticate(self, digest: str):
        row = self.db.conn.execute('''
            SELECT b.cookie_id, b.user_id, b.account_unb, b.expires_at,
                   b.last_sync_at, b.last_cookie_hash, c.value, c.user_id, u.is_active,
                   b.pending_cookie_hash
            FROM desktop_bridge_tokens b
            JOIN cookies c ON c.id = b.cookie_id
            JOIN users u ON u.id = b.user_id
            WHERE b.token_hash = ?
        ''', (digest,)).fetchone()
        valid = bool(row and row[3] > self.clock() and row[1] == row[7] and row[8])
        if valid:
            try:
                valid = _cookie_fields(row[6]).get('unb') == row[2]
            except HTTPException:
                valid = False
        if not valid:
            if row:
                self.db.conn.execute('DELETE FROM desktop_bridge_tokens WHERE token_hash = ?', (digest,))
                self.db.conn.commit()
            raise HTTPException(401, '电脑连接已失效，请在账号页面重新生成连接令牌')
        return row

    def sync(self, token: str, value: str) -> dict:
        if not TOKEN_PATTERN.fullmatch(token):
            raise HTTPException(401, '无效的电脑连接令牌')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            with self.db.lock:
                row = self._authenticate(digest)
                cleaned = _normalize_cookie(value)
                if _cookie_fields(cleaned)['unb'] != row[2]:
                    raise HTTPException(403, '客户端登录账号与绑定账号不一致，未更新')
                fingerprint = _fingerprint(cleaned)
                now = self.clock()
                pending = row[9] is not None
                unchanged = not pending and fingerprint == row[5]
                if not unchanged and not pending:
                    try:
                        unchanged = fingerprint == _fingerprint(_normalize_cookie(row[6]))
                    except HTTPException:
                        pass
                if unchanged:
                    self.db.conn.execute('''
                        UPDATE desktop_bridge_tokens SET last_seen_at = ?,
                            last_sync_at = COALESCE(last_sync_at, ?), last_cookie_hash = ?
                        WHERE token_hash = ?
                    ''', (now, now, fingerprint, digest))
                    self.db.conn.commit()
                    return {'success': True, 'changed': False, 'message': '电脑已连接，会话未变化'}
                if row[4] is not None and now - row[4] < MIN_SYNC_INTERVAL:
                    retry = max(1, math.ceil(MIN_SYNC_INTERVAL - (now - row[4])))
                    raise HTTPException(429, '会话更新较频繁，稍后会自动重试', headers={'Retry-After': str(retry)})
                # Saving the Cookie can succeed before its runtime restart fails.
                # Persist the unfinished sync so a retry isn't mistaken for a
                # completed update merely because the database already matches.
                self.db.conn.execute('''
                    UPDATE desktop_bridge_tokens SET pending_cookie_hash = ? WHERE token_hash = ?
                ''', (fingerprint, digest))
                self.db.conn.commit()
            # The existing importer rechecks ownership and unb before writing and
            # uses the normal account restart and risk-guard reset flow.
            try:
                self.import_session(row[0], cleaned, {'user_id': row[1]})
            except HTTPException as error:
                # Never propagate a downstream exception which may contain values.
                raise HTTPException(error.status_code, '会话更新失败，请检查服务器账号状态后重试') from None
            except Exception:
                raise HTTPException(503, '会话更新失败，请检查服务器账号状态后重试') from None
            with self.db.lock:
                self._authenticate(digest)
                now = self.clock()
                self.db.conn.execute('''
                    UPDATE desktop_bridge_tokens
                    SET last_seen_at = ?, last_sync_at = ?, last_cookie_hash = ?, pending_cookie_hash = NULL
                    WHERE token_hash = ?
                ''', (now, now, fingerprint, digest))
                self.db.conn.commit()
            return {'success': True, 'changed': True, 'message': '会话已自动同步，请查看服务器账号连接状态'}

    def query_settings(self, cid: str, user_id: int, enabled: bool):
        with self.lock, self.db.lock:
            self._owned_account(cid, user_id)
            row = self.db.conn.execute('SELECT token_hash FROM desktop_bridge_tokens WHERE cookie_id=?', (cid,)).fetchone()
            if not row:
                raise HTTPException(409, '请先生成电脑连接令牌')
            self._authenticate(row[0])
            self.db.conn.execute('UPDATE desktop_bridge_tokens SET query_enabled=? WHERE cookie_id=?', (int(enabled), cid))
            if not enabled:
                self.db.conn.execute('DELETE FROM desktop_bridge_jobs WHERE cookie_id=?', (cid,))
            self.db.conn.commit()
        return self.status(cid, user_id)

    def _query_identity(self, token: str, identity: str):
        if not TOKEN_PATTERN.fullmatch(token):
            raise HTTPException(401, '无效的电脑连接令牌')
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self._authenticate(digest)
        if identity != row[2]:
            raise HTTPException(403, '客户端账号与绑定账号不一致')
        return digest

    def poll_query(self, token: str, data: DesktopQueryPoll):
        with self.lock, self.db.lock:
            digest = self._query_identity(token, data.account_unb)
            now = self.clock()
            self.db.conn.execute('DELETE FROM desktop_bridge_jobs WHERE deadline < ?', (now,))
            self.db.conn.execute('''
                UPDATE desktop_bridge_tokens SET query_ready=?, last_query_seen_at=? WHERE token_hash=?
            ''', (int(data.sdk_available), now, digest))
            enabled = self.db.conn.execute('SELECT query_enabled FROM desktop_bridge_tokens WHERE token_hash=?', (digest,)).fetchone()[0]
            row = None
            if enabled and data.sdk_available:
                # UPDATE ... RETURNING claims at most one job atomically across processes.
                row = self.db.conn.execute('''
                    UPDATE desktop_bridge_jobs SET claimed=1 WHERE id=(
                        SELECT id FROM desktop_bridge_jobs
                        WHERE token_hash=? AND claimed=0 AND deadline>?
                        ORDER BY deadline LIMIT 1
                    ) AND claimed=0 RETURNING id, request
                ''', (digest, now)).fetchone()
            self.db.conn.commit()
            return {'success': True, 'query_enabled': bool(enabled),
                    'job': {'id': row[0], **json.loads(row[1])} if row else None}

    def complete_query(self, token: str, data: DesktopQueryResult):
        with self.lock, self.db.lock:
            digest = self._query_identity(token, data.account_unb)
            if (data.error is None) == (data.result is None) or (data.error is not None and data.error not in ERRORS):
                raise HTTPException(400, '查询结果格式无效')
            result = data.result
            if result is not None:
                if not isinstance(result.get('ret'), list) or not all(isinstance(x, str) for x in result['ret']):
                    raise HTTPException(400, '客户端查询响应格式无效')
                # Transport metadata / headers / credentials are never returned to callers.
                result = {'ret': result['ret'], 'data': result.get('data')}
            try:
                encoded = json.dumps({'error': data.error, 'result': result}, ensure_ascii=True, allow_nan=False)
            except (ValueError, TypeError):
                raise HTTPException(400, '查询结果格式无效') from None
            if len(encoded.encode()) > MAX_RESULT_BYTES:
                raise HTTPException(413, '查询结果过大')
            updated = self.db.conn.execute('''
                UPDATE desktop_bridge_jobs SET response=?
                WHERE id=? AND token_hash=? AND claimed=1 AND deadline>?
                    AND response IS NULL AND EXISTS (
                        SELECT 1 FROM desktop_bridge_tokens WHERE token_hash=? AND query_enabled=1
                    )
            ''', (encoded, data.job_id, digest, self.clock(), digest))
            self.db.conn.commit()
            return {'success': True, 'accepted': updated.rowcount == 1}


def create_desktop_bridge_router(get_current_user: Callable, db_manager: Any,
                                 import_session: Callable, *, clock: Callable = time.time) -> APIRouter:
    router = APIRouter()
    service = DesktopBridgeService(db_manager, import_session, clock)
    bearer = HTTPBearer(auto_error=False)

    def no_cache(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'

    @router.get('/cookies/{cid}/desktop-bridge')
    def get_status(cid: str, response: Response, user: dict = Depends(get_current_user)):
        no_cache(response)
        return service.status(cid, user['user_id'])

    @router.post('/cookies/{cid}/desktop-bridge')
    def pair(cid: str, response: Response, user: dict = Depends(get_current_user)):
        no_cache(response)
        return service.pair(cid, user['user_id'])

    @router.delete('/cookies/{cid}/desktop-bridge')
    def revoke(cid: str, response: Response, user: dict = Depends(get_current_user)):
        no_cache(response)
        return service.revoke(cid, user['user_id'])

    @router.post('/desktop-bridge/session')
    def sync(data: DesktopSessionRequest, response: Response,
             credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        no_cache(response)
        if not credentials or credentials.scheme.lower() != 'bearer':
            raise HTTPException(401, '需要电脑连接令牌')
        return service.sync(credentials.credentials, data.value.get_secret_value())

    @router.put('/cookies/{cid}/desktop-bridge/queries')
    def query_settings(cid: str, data: DesktopQuerySettings, response: Response,
                       user: dict = Depends(get_current_user)):
        no_cache(response)
        return service.query_settings(cid, user['user_id'], data.enabled)

    @router.post('/desktop-bridge/queries/poll')
    def poll_query(data: DesktopQueryPoll, response: Response,
                   credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        no_cache(response)
        if not credentials:
            raise HTTPException(401, '需要电脑连接令牌')
        return service.poll_query(credentials.credentials, data)

    @router.post('/desktop-bridge/queries/result')
    def complete_query(data: DesktopQueryResult, response: Response,
                       credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        no_cache(response)
        if not credentials:
            raise HTTPException(401, '需要电脑连接令牌')
        return service.complete_query(credentials.credentials, data)

    return router
