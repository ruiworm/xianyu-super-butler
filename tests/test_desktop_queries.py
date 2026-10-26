import asyncio
import hashlib
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.db_manager import DBManager
from app.desktop_bridge import (DesktopBridgeService, DesktopQueryPoll,
                                DesktopQueryResult)
from app.desktop_queries import DesktopQueryBroker, DesktopQueryError
from desktop_bridge.bridge import BridgeWorker, CDPClient
from desktop_bridge.queries import query_expression
from utils.xianyu_seller_api import XianyuSellerAPI


API = 'mtop.taobao.idle.trade.merchant.sold.get'
SUCCESS = {'ret': ['SUCCESS::调用成功'], 'data': {'module': {'items': [{'orderId': '123'}]}}}


class DesktopQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DBManager(self.temp.name + '/test.db')
        self.db.sql_log_enabled = False
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('query-test','unb=123; _m_h5_tk=t',1)")
        self.db.conn.commit()
        self.service = DesktopBridgeService(self.db, Mock())
        self.broker = DesktopQueryBroker(self.db)
        self.token = self.service.pair('query-test', 1)['token']
        self.service.query_settings('query-test', 1, True)
        self.poll()
        self.guard = Mock(is_blocked=False)
        self.guard_patch = patch('app.desktop_queries.risk_control.registry.get', return_value=self.guard)
        self.guard_patch.start()

    async def asyncTearDown(self):
        self.guard_patch.stop()
        self.db.close()
        self.temp.cleanup()

    def poll(self, token=None, identity='123', ready=True):
        return self.service.poll_query(token or self.token, DesktopQueryPoll(account_unb=identity, sdk_available=ready))

    async def queued(self):
        task = asyncio.create_task(self.broker.request('query-test', API, {'pageNumber': 1}, timeout=2))
        await asyncio.sleep(.01)
        return task, self.poll()['job']

    async def test_result_returns_to_correct_caller_once_and_is_removed(self):
        task, job = await self.queued()
        self.assertEqual(job['account_unb'], '123')
        self.assertIsNone(self.poll()['job'])
        payload = DesktopQueryResult(account_unb='123', job_id=job['id'], result={**SUCCESS, 'headers': {'cookie': 'must-not-pass'}})
        self.assertTrue(self.service.complete_query(self.token, payload)['accepted'])
        self.assertFalse(self.service.complete_query(self.token, payload)['accepted'])
        self.assertEqual(await task, SUCCESS)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM desktop_bridge_jobs').fetchone()[0], 0)

    async def test_offline_and_unsupported_sdk_fail_without_server_fallback(self):
        self.poll(ready=False)
        with self.assertRaisesRegex(DesktopQueryError, '未就绪'):
            await self.broker.request('query-test', API, {})
        self.service.query_settings('query-test', 1, False)
        self.assertIsNone(await self.broker.request('query-test', API, {}))
        self.assertIsNone(await self.broker.request('query-test', 'mtop.taobao.idle.merchant.rate.create', {}))

    async def test_identity_and_pairing_rotation_cancel_inflight_queries(self):
        with self.assertRaises(HTTPException) as caught:
            self.poll(identity='another-account')
        self.assertEqual(caught.exception.status_code, 403)
        task, job = await self.queued()
        replacement = self.service.pair('query-test', 1)['token']
        with self.assertRaises(HTTPException):
            self.service.complete_query(self.token, DesktopQueryResult(account_unb='123', job_id=job['id'], result=SUCCESS))
        # A new token cannot complete jobs issued to the old pairing.
        self.assertFalse(self.service.complete_query(replacement, DesktopQueryResult(account_unb='123', job_id=job['id'], result=SUCCESS))['accepted'])
        with self.assertRaisesRegex(DesktopQueryError, '取消'):
            await task

    async def test_timeout_cancellation_and_user_mode_change_clear_mailbox(self):
        with self.assertRaisesRegex(DesktopQueryError, '超时'):
            await self.broker.request('query-test', API, {}, timeout=.02)
        task, _ = await self.queued()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        task, _ = await self.queued()
        self.service.query_settings('query-test', 1, False)
        with self.assertRaisesRegex(DesktopQueryError, '取消'):
            await task
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM desktop_bridge_jobs').fetchone()[0], 0)

    async def test_errors_and_platform_challenge_are_not_success(self):
        task, job = await self.queued()
        self.service.complete_query(self.token, DesktopQueryResult(account_unb='123', job_id=job['id'], error='sdk_unavailable'))
        with self.assertRaisesRegex(DesktopQueryError, '未提供'):
            await task
        task, job = await self.queued()
        challenge = {'ret': ['FAIL_SYS_USER_VALIDATE::需要验证'], 'data': {}}
        self.service.complete_query(self.token, DesktopQueryResult(account_unb='123', job_id=job['id'], result=challenge))
        self.assertEqual(await task, challenge)
        self.guard.trip.assert_called_once()

    async def test_another_account_cannot_claim_or_submit_task(self):
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('other','unb=456; _m_h5_tk=t',1)")
        self.db.conn.commit()
        other = self.service.pair('other', 1)['token']
        self.service.query_settings('other', 1, True)
        task, job = await self.queued()
        self.assertIsNone(self.poll(other, identity='456')['job'])
        self.assertFalse(self.service.complete_query(other, DesktopQueryResult(account_unb='456', job_id=job['id'], result=SUCCESS))['accepted'])
        self.service.complete_query(self.token, DesktopQueryResult(account_unb='123', job_id=job['id'], result=SUCCESS))
        self.assertEqual(await task, SUCCESS)

    async def test_seller_query_uses_desktop_and_never_opens_http_on_failure(self):
        api = XianyuSellerAPI('query-test', 'unb=123; _m_h5_tk=t')
        api._ensure_session = AsyncMock(side_effect=AssertionError('must not call server HTTP'))
        with patch('utils.xianyu_seller_api.desktop_query_if_enabled', side_effect=self.broker.request):
            task = asyncio.create_task(api.get_sold_orders())
            await asyncio.sleep(.01)
            job = self.poll()['job']
            self.service.complete_query(self.token, DesktopQueryResult(account_unb='123', job_id=job['id'], result=SUCCESS))
            self.assertEqual((await task)['items'], [{'orderId': '123'}])
            self.poll(ready=False)
            with self.assertRaises(DesktopQueryError):
                await api.get_sold_orders()
        api._ensure_session.assert_not_called()


class DesktopQueryClientTests(unittest.TestCase):
    def test_only_fixed_read_apis_are_executable(self):
        for api in ('https://attacker.example', 'mtop.taobao.idle.merchant.rate.create', 'eval'):
            with self.assertRaises(ValueError):
                query_expression({'api': api, 'version': '1.0', 'data': {}})

    def test_account_change_discards_result(self):
        cdp = CDPClient()
        cdp._query_targets = Mock(return_value=[{'url': 'https://seller.goofish.com/'}])
        cdp._evaluate_target = Mock(side_effect=[True, {'result': SUCCESS}])
        cdp._read_target = Mock(side_effect=['unb=123; _m_h5_tk=t', 'unb=456; _m_h5_tk=x'])
        outcome = cdp.execute_query({'api': API, 'version': '1.0', 'data': {}, 'account_unb': '123'})
        self.assertEqual(outcome, {'error': 'account_changed'})

    def test_worker_polls_between_cookie_heartbeats(self):
        cdp, uploader = Mock(), Mock()
        cdp.read_cookie.return_value = 'unb=123; _m_h5_tk=t'
        cdp.query_sdk_available.return_value = True
        cdp.execute_query.return_value = {'result': SUCCESS}
        uploader.upload.return_value = {'changed': False}
        uploader.poll_query.return_value = {'query_enabled': True, 'job': None}
        worker = BridgeWorker(cdp, uploader, Mock())
        worker.step(0)
        worker.step(3)
        uploader.poll_query.return_value = {'query_enabled': True, 'job': {'id': 'job1', 'account_unb': '123'}}
        worker.step(6)
        uploader.upload.assert_called_once()
        uploader.complete_query.assert_called_once_with('123', 'job1', {'result': SUCCESS})
