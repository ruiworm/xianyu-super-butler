"""Exercise signed HTTP requests and token renewal against a local MTOP stub."""
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

from utils import risk_control
from utils.xianyu_seller_api import SellerApiError, XianyuSellerAPI
from utils.xianyu_utils import trans_cookies


class SellerSessionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.responses = []

        async def endpoint(request):
            self.requests.append({
                'query': dict(request.query),
                'body': dict(await request.post()),
                'cookie': request.headers.get('Cookie'),
                'origin': request.headers.get('Origin'),
            })
            response = self.responses.pop(0)
            return web.json_response(response['data'], headers=response.get('headers'))

        app = web.Application()
        app.router.add_post('/h5/{api}/{version}/', endpoint)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.api = XianyuSellerAPI('test-session', 'unb=123; _m_h5_tk=first_999; cookie2=session')
        self.api.BASE_URL = f'http://127.0.0.1:{port}/h5/{{api}}/{{version}}/'
        self.guard = Mock(is_blocked=False)
        self.guard.acquire = AsyncMock()
        self.guard_patch = patch('utils.xianyu_seller_api.risk_control.registry.get', return_value=self.guard)
        self.guard_patch.start()

    async def asyncTearDown(self):
        self.guard_patch.stop()
        await self.api.close()
        await self.runner.cleanup()

    def assert_signed_request(self, request, token):
        query = request['query']
        expected = hashlib.md5(
            f"{token}&{query['t']}&34839810&{request['body']['data']}".encode('utf-8')
        ).hexdigest()
        self.assertEqual(query['sign'], expected)
        self.assertEqual(request['origin'], 'https://seller.goofish.com')
        self.assertNotIn('_m_h5_sign', query)

    async def test_cookie_direct_call_and_updated_session_use_current_signature(self):
        self.responses = [{'data': {'ret': ['SUCCESS::调用成功'], 'data': {'ok': True}}}] * 2
        await self.api._call('mtop.test.session', {'title': '商品'})
        self.assert_signed_request(self.requests[0], 'first')
        self.assertEqual(json.loads(self.requests[0]['body']['data']), {'title': '商品'})
        self.assertIn('cookie2=session', self.requests[0]['cookie'])
        self.api.update_cookies('unb=123; _m_h5_tk=desktop_999; cookie2=new-session')
        await self.api._call('mtop.test.session', {'title': '商品'})
        self.assert_signed_request(self.requests[1], 'desktop')
        self.assertIn('cookie2=new-session', self.requests[1]['cookie'])

    async def test_experimental_hmac_is_used_by_actual_http_request(self):
        self.responses = [{'data': {'ret': ['SUCCESS::调用成功']}}]
        with patch.dict(os.environ, {'XIANYU_SIGN_ALGORITHM': 'hmac-sha1'}):
            with self.assertWarnsRegex(RuntimeWarning, 'unverified'):
                await self.api._call('mtop.test.session', {'title': '商品'})
        request = self.requests[0]
        message = f"first&{request['query']['t']}&34839810&{request['body']['data']}"
        expected = hmac.new(b'first', message.encode('utf-8'), hashlib.sha1).hexdigest()
        self.assertEqual(request['query']['sign'], expected)
        self.assertEqual(len(self.requests), 1)

    async def test_expired_token_is_renewed_and_resigned_once(self):
        self.responses = [
            {'data': {'ret': ['FAIL_SYS_TOKEN_EXOIRED::令牌过期']},
             'headers': {'Set-Cookie': '_m_h5_tk=renewed_999; Path=/; HttpOnly'}},
            {'data': {'ret': ['SUCCESS::调用成功'], 'data': {'ok': True}}},
        ]
        result = await self.api._call('mtop.test.session', {})
        self.assertTrue(result['data']['ok'])
        self.assertEqual(len(self.requests), 2)
        self.assert_signed_request(self.requests[0], 'first')
        self.assert_signed_request(self.requests[1], 'renewed')
        self.assertIn('_m_h5_tk=renewed_999', self.requests[1]['cookie'])

    async def test_desktop_cookie_without_separator_spaces_signs_and_renews(self):
        raw = ' unb=123;_m_h5_tk=desktop_999;  cookie2=encoded==; '
        self.assertEqual(trans_cookies(raw), {
            'unb': '123', '_m_h5_tk': 'desktop_999', 'cookie2': 'encoded==',
        })
        self.api.update_cookies(raw.strip())
        self.responses = [
            {'data': {'ret': ['FAIL_SYS_TOKEN_EXPIRED::令牌过期']},
             'headers': {'Set-Cookie': '_m_h5_tk=renewed_999; Path=/; HttpOnly'}},
            {'data': {'ret': ['SUCCESS::调用成功']}},
        ]
        await self.api._call('mtop.test.session', {})
        self.assert_signed_request(self.requests[0], 'desktop')
        self.assert_signed_request(self.requests[1], 'renewed')
        self.assertIn('unb=123', self.requests[1]['cookie'])
        self.assertIn('cookie2=encoded==', self.requests[1]['cookie'])

    async def test_api_challenge_stops_requests_until_manual_recovery(self):
        self.responses = [{'data': {'ret': ['FAIL_SYS_USER_VALIDATE::需要验证']}}]
        with self.assertRaises(SellerApiError):
            await self.api._call('mtop.test.session', {})
        self.guard.trip.assert_called_once()
        self.guard.is_blocked = True
        with self.assertRaises(risk_control.RiskControlBlocked):
            await self.api._call('mtop.test.session', {})
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
