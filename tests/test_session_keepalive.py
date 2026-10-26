import asyncio
import hashlib
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from XianyuAutoAsync import XianyuLive


class SessionKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    def make_live(self, browser_refresh='false'):
        with (patch.dict(os.environ, {'BROWSER_COOKIE_REFRESH_ENABLED': browser_refresh}),
              patch.object(XianyuLive, '_init_order_status_handler'),
              patch.object(XianyuLive, '_register_instance')):
            return XianyuLive('unb=123; _m_h5_tk=old_999', cookie_id='keepalive-test', user_id=1)

    async def test_browser_refresh_is_opt_in_and_does_not_run_immediately(self):
        live = self.make_live()
        live._execute_cookie_refresh = AsyncMock()
        live._interruptible_sleep = AsyncMock(side_effect=asyncio.CancelledError)
        with patch('app.cookie_manager.manager', None):
            with self.assertRaises(asyncio.CancelledError):
                await live.cookie_refresh_loop()
        live._execute_cookie_refresh.assert_not_called()
        opted = self.make_live('true')
        self.assertTrue(opted.cookie_refresh_enabled)
        opted._execute_cookie_refresh = AsyncMock()
        opted._interruptible_sleep = AsyncMock(side_effect=asyncio.CancelledError)
        with patch('app.cookie_manager.manager', None):
            with self.assertRaises(asyncio.CancelledError):
                await opted.cookie_refresh_loop()
        opted._execute_cookie_refresh.assert_not_called()

    async def test_slow_or_failed_browser_refresh_keeps_existing_heartbeat(self):
        for result in (False, asyncio.TimeoutError()):
            live = self.make_live('true')
            heartbeat = Mock()
            heartbeat.done.return_value = False
            live.heartbeat_task = heartbeat
            live.last_message_received_time = 123
            live._refresh_cookies_via_browser = AsyncMock()
            if isinstance(result, Exception):
                live._refresh_cookies_via_browser.side_effect = result
            else:
                live._refresh_cookies_via_browser.return_value = result
            with patch('utils.risk_control.registry.get', return_value=Mock(is_blocked=False)):
                await live._execute_cookie_refresh(456)
            heartbeat.cancel.assert_not_called()
            self.assertIs(live.heartbeat_task, heartbeat)
            self.assertEqual(live.last_message_received_time, 123)

    async def test_manual_verification_and_risk_cooldown_skip_browser_refresh(self):
        for manual, blocked in ((True, False), (False, True)):
            live = self.make_live('true')
            live.manual_captcha_in_progress = manual
            live._refresh_cookies_via_browser = AsyncMock()
            with patch('utils.risk_control.registry.get', return_value=Mock(is_blocked=blocked)):
                await live._execute_cookie_refresh(456)
            live._refresh_cookies_via_browser.assert_not_called()

    async def test_token_request_reloads_latest_database_cookie_before_signing(self):
        live = self.make_live()
        response = MagicMock(status=200, headers={})
        response.json = AsyncMock(return_value={'ret': ['SUCCESS::调用成功'], 'data': {'accessToken': 'im-token'}})
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.post.return_value = response
        with (patch('app.db_manager.db_manager.get_cookie_details', return_value={'value': 'unb=123;_m_h5_tk=new_999'}),
              patch('aiohttp.ClientSession', return_value=session),
              patch('utils.risk_control.registry.get', return_value=Mock(is_blocked=False))):
            self.assertEqual(await live.refresh_token(), 'im-token')
        request = session.post.call_args.kwargs
        self.assertEqual(request['headers']['cookie'], 'unb=123;_m_h5_tk=new_999')
        params = request['params']
        expected = hashlib.md5(f"new&{params['t']}&34839810&{request['data']['data']}".encode()).hexdigest()
        self.assertEqual(params['sign'], expected)
        self.assertEqual(live.cookies['_m_h5_tk'], 'new_999')


if __name__ == '__main__':
    unittest.main()
