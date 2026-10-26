"""Short-lived, account-bound query mailbox shared by server threads/processes.

Only the paired desktop executes queries. An unavailable desktop never silently
falls back to a request from the server when desktop queries are enabled.
"""
import asyncio
import json
import secrets
import time

from desktop_bridge.protocol import READ_APIS, validate_request
from utils.xianyu_utils import trans_cookies
from utils import risk_control


class DesktopQueryError(RuntimeError):
    pass


class DesktopQueryBroker:
    def __init__(self, db, clock=time.time):
        self.db = db
        self.clock = clock

    async def request(self, cid, api, data, version='1.0', timeout=25):
        if api not in READ_APIS:
            return None
        with self.db.lock:
            row = self.db.conn.execute('''
                SELECT b.query_enabled, b.query_ready, b.last_query_seen_at,
                       b.token_hash, b.account_unb, b.expires_at,
                       c.value, b.user_id, c.user_id, u.is_active
                FROM desktop_bridge_tokens b
                JOIN cookies c ON c.id=b.cookie_id
                JOIN users u ON u.id=b.user_id WHERE b.cookie_id=?
            ''', (cid,)).fetchone()
            if not row or not row[0]:
                return None
            guard = risk_control.registry.get(cid)
            if guard.is_blocked:
                raise risk_control.RiskControlBlocked(cid, guard.remaining_seconds, guard.last_hit_reason)
            now = self.clock()
            if row[5] <= now or row[7] != row[8] or not row[9] or trans_cookies(row[6]).get('unb') != row[4]:
                raise DesktopQueryError('桌面连接凭据已失效，请重新连接')
            if not row[1] or row[2] is None or now - row[2] > 45:
                raise DesktopQueryError('桌面查询未就绪，请保持连接工具运行并打开客户端商品或订单页面')
            validate_request(api, version, data)
            self.db.conn.execute('DELETE FROM desktop_bridge_jobs WHERE deadline < ?', (now,))
            counts = self.db.conn.execute('''
                SELECT COUNT(*), COALESCE(SUM(token_hash=?),0) FROM desktop_bridge_jobs
            ''', (row[3],)).fetchone()
            if counts[0] >= 512 or counts[1] >= 8:
                self.db.conn.commit()
                raise DesktopQueryError('桌面查询繁忙，请稍后重试')
            job_id = secrets.token_urlsafe(24)
            request = {'api': api, 'version': version, 'data': data, 'account_unb': row[4]}
            self.db.conn.execute('''
                INSERT INTO desktop_bridge_jobs(id,cookie_id,token_hash,request,deadline)
                VALUES(?,?,?,?,?)
            ''', (job_id, cid, row[3], json.dumps(request, ensure_ascii=True), now + timeout))
            self.db.conn.commit()
        try:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                with self.db.lock:
                    pending = self.db.conn.execute('''
                        SELECT j.response FROM desktop_bridge_jobs j
                        JOIN desktop_bridge_tokens b ON b.token_hash=j.token_hash
                        JOIN cookies c ON c.id=b.cookie_id
                        JOIN users u ON u.id=b.user_id
                        WHERE j.id=? AND b.query_enabled=1 AND b.expires_at>?
                            AND c.user_id=b.user_id AND u.is_active=1
                    ''', (job_id, self.clock())).fetchone()
                if not pending:
                    raise DesktopQueryError('桌面查询已取消，请检查连接是否被撤销或更换')
                if pending[0] is not None:
                    outcome = json.loads(pending[0])
                    if outcome.get('error'):
                        messages = {
                            'sdk_unavailable': '客户端页面未提供受支持的查询接口，请打开商品或订单页面',
                            'verification_required': '请在闲鱼卖家客服中手动完成验证',
                            'account_changed': '客户端账号已变化，本次查询已停止',
                            'response_too_large': '客户端返回数据过大，请减小查询分页数量',
                        }
                        raise DesktopQueryError(messages.get(outcome['error'], '客户端查询失败，请检查桌面连接'))
                    result = outcome['result']
                    if risk_control.is_risk_control_error('; '.join(result.get('ret', []))):
                        guard.trip('; '.join(result['ret']))
                    return result
                await asyncio.sleep(0.1)
            raise DesktopQueryError('桌面查询超时，请检查客户端和连接工具')
        finally:
            with self.db.lock:
                self.db.conn.execute('DELETE FROM desktop_bridge_jobs WHERE id=?', (job_id,))
                self.db.conn.commit()


async def desktop_query_if_enabled(cid, api, data, version='1.0'):
    from app.db_manager import db_manager
    return await DesktopQueryBroker(db_manager).request(cid, api, data, version)
