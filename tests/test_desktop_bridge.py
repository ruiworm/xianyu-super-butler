import hashlib
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.db_manager import DBManager
from app.desktop_bridge import DesktopBridgeService, TOKEN_LIFETIME, create_desktop_bridge_router


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'test.db')
        self.db = DBManager(self.path)
        self.db.sql_log_enabled = False
        self.db.conn.execute("INSERT INTO cookies (id,value,user_id) VALUES ('seller','unb=123; _m_h5_tk=old',1)")
        self.db.conn.commit()
        self.user = {'user_id': 1}
        self.now = 1700000000.0
        self.importer = Mock(side_effect=self.save)
        self.make_client()

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.temp.cleanup()

    def save(self, cid, value, user):
        self.assertEqual(user['user_id'], 1)
        self.assertTrue(self.db.update_cookie_account_info(cid, cookie_value=value))
        return {'success': True}

    def make_client(self):
        def auth():
            if self.user is None:
                raise HTTPException(401, 'login required')
            return self.user

        app = FastAPI()
        app.include_router(create_desktop_bridge_router(auth, self.db, self.importer, clock=lambda: self.now))
        self.client = TestClient(app)

    def pair(self):
        result = self.client.post('/cookies/seller/desktop-bridge')
        result.raise_for_status()
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        return result.json()['token']

    def sync(self, token, value='unb=123; _m_h5_tk=new'):
        return self.client.post('/desktop-bridge/session', headers={'Authorization': 'Bearer ' + token}, json={'value': value})

    def test_pair_status_scoped_auth_and_no_secret_disclosure(self):
        status = self.client.get('/cookies/seller/desktop-bridge').json()
        self.assertFalse(status['enabled'])
        self.assertIsNone(status['last_seen_at'])
        token = self.pair()
        row = self.db.conn.execute('SELECT token_hash FROM desktop_bridge_tokens').fetchone()
        self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, str(row))
        result = self.client.get('/cookies/seller/desktop-bridge')
        self.assertTrue(result.json()['enabled'])
        self.assertTrue(result.json()['expires_at'].endswith('Z'))
        self.assertNotIn('token', result.text)
        self.user = {'user_id': 999}
        for method in (self.client.get, self.client.post, self.client.delete):
            self.assertEqual(method('/cookies/seller/desktop-bridge').status_code, 403)
        self.user = None
        self.assertEqual(self.client.post('/cookies/seller/desktop-bridge').status_code, 401)
        self.assertEqual(self.sync('ordinary-login-token').status_code, 401)
        self.assertEqual(self.client.post('/desktop-bridge/session', json={'value': 'unb=123'}).status_code, 401)

    def test_cookie_identity_validation_and_credential_free_errors(self):
        token = self.pair()
        wrong_account = self.sync(token, 'unb=456; _m_h5_tk=secret')
        self.assertEqual(wrong_account.status_code, 403)
        self.assertNotIn('secret', wrong_account.text)
        for value in ('unb=123', 'unb=123; unb=456; _m_h5_tk=secret',
                      'unb=123; _m_h5_tk=secret\nAnother:header', 'x' * 32001):
            response = self.sync(token, value)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('secret', response.text)
            self.assertNotIn(token, response.text)
        self.importer.assert_not_called()
        self.assertEqual(self.db.get_cookie('seller'), 'unb=123; _m_h5_tk=old')

    def test_idempotence_normalization_challenge_cleanup_and_server_refresh(self):
        token = self.pair()
        result = self.sync(token, 'Cookie: unb=123;_m_h5_tk=new;x5sec=passed;x5step=challenge')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['changed'])
        self.assertNotIn('x5step', self.db.get_cookie('seller'))
        self.now += 1
        result = self.sync(token, 'x5step=challenge; x5sec=passed; _m_h5_tk=new; unb=123')
        self.assertFalse(result.json()['changed'])
        # A stationary desktop must not roll back tokens refreshed by the server.
        self.db.update_cookie_account_info('seller', cookie_value='unb=123; _m_h5_tk=server-refreshed')
        self.now += 60
        self.assertFalse(self.sync(token, 'unb=123; _m_h5_tk=new; x5sec=passed').json()['changed'])
        self.assertIn('server-refreshed', self.db.get_cookie('seller'))
        self.importer.assert_called_once()
        self.assertIsNotNone(self.client.get('/cookies/seller/desktop-bridge').json()['last_sync_at'])

    def test_unchanged_initial_cookie_is_acknowledged_without_restart(self):
        token = self.pair()
        self.assertFalse(self.sync(token, '_m_h5_tk=old; unb=123').json()['changed'])
        self.importer.assert_not_called()

    def test_rate_limit_does_not_restart_and_can_retry(self):
        token = self.pair()
        self.assertEqual(self.sync(token).status_code, 200)
        self.now += 2
        result = self.sync(token, 'unb=123; _m_h5_tk=next')
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers['Retry-After'], '13')
        self.assertEqual(self.importer.call_count, 1)
        self.now += 13
        self.assertTrue(self.sync(token, 'unb=123; _m_h5_tk=next').json()['changed'])

    def test_rotation_revocation_and_expiration(self):
        first = self.pair()
        second = self.pair()
        self.assertEqual(self.sync(first).status_code, 401)
        self.assertEqual(self.sync(second).status_code, 200)
        self.client.delete('/cookies/seller/desktop-bridge').raise_for_status()
        self.assertEqual(self.sync(second).status_code, 401)
        third = self.pair()
        self.now += TOKEN_LIFETIME
        self.assertFalse(self.client.get('/cookies/seller/desktop-bridge').json()['enabled'])
        self.assertEqual(self.sync(third).status_code, 401)

    def test_pairing_and_fingerprint_persist_across_database_reopen(self):
        token = self.pair()
        self.sync(token).raise_for_status()
        self.client.close()
        self.db.close()
        self.db = DBManager(self.path)
        self.make_client()
        self.assertTrue(self.client.get('/cookies/seller/desktop-bridge').json()['enabled'])
        self.assertFalse(self.sync(token).json()['changed'])
        self.assertEqual(self.importer.call_count, 1)

    def test_deleted_recreated_and_replaced_account_cannot_use_old_token(self):
        token = self.pair()
        self.assertTrue(self.db.delete_cookie('seller'))
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM desktop_bridge_tokens').fetchone()[0], 0)
        self.db.save_cookie('seller', 'unb=123; _m_h5_tk=old', 1)
        self.assertEqual(self.sync(token).status_code, 401)
        token = self.pair()
        self.db.update_cookie_account_info('seller', cookie_value='unb=456; _m_h5_tk=other')
        self.db.update_cookie_account_info('seller', cookie_value='unb=123; _m_h5_tk=old')
        self.assertEqual(self.sync(token).status_code, 401)

    def test_owner_change_and_disabled_user_reject_sync(self):
        token = self.pair()
        self.db.conn.execute("UPDATE cookies SET user_id = 999 WHERE id = 'seller'")
        self.db.conn.commit()
        self.assertEqual(self.sync(token).status_code, 401)
        self.db.conn.execute("UPDATE cookies SET user_id = 1 WHERE id = 'seller'")
        self.db.conn.commit()
        token = self.pair()
        self.db.set_user_active(1, False)
        self.assertEqual(self.sync(token).status_code, 401)

    def test_backup_excludes_token_and_restore_revokes_pairing(self):
        token = self.pair()
        for user_id in (None, 1):
            backup = self.db.export_backup(user_id=user_id)
            self.assertNotIn('desktop_bridge_tokens', backup['data'])
            self.assertNotIn(token, str(backup))
        self.assertTrue(self.db.import_backup(backup, user_id=1))
        self.assertEqual(self.sync(token).status_code, 401)

    def test_downstream_failure_is_redacted_and_not_marked_synced(self):
        token = self.pair()
        self.importer.side_effect = HTTPException(503, 'private-cookie-value')
        result = self.sync(token)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private-cookie-value', result.text)
        self.assertIsNone(self.client.get('/cookies/seller/desktop-bridge').json()['last_sync_at'])

    def test_restart_failure_after_save_retries_even_after_server_restart(self):
        token = self.pair()

        def failed_restart(cid, value, user):
            self.save(cid, value, user)
            raise TimeoutError('restart timed out')

        self.importer.side_effect = failed_restart
        self.assertEqual(self.sync(token).status_code, 503)
        self.assertIn('_m_h5_tk=new', self.db.get_cookie('seller'))
        self.client.close()
        self.db.close()
        self.db = DBManager(self.path)
        self.make_client()
        self.importer.side_effect = self.save
        self.assertTrue(self.sync(token).json()['changed'])
        self.assertEqual(self.importer.call_count, 2)
        self.assertFalse(self.sync(token).json()['changed'])

    def test_real_importer_repairs_runtime_after_database_write_succeeded(self):
        from types import SimpleNamespace
        from app import reply_server

        manager = SimpleNamespace(cookies={'seller': 'unb=123; _m_h5_tk=old'})

        def restart(cid, value, save_to_db=False):
            if manager.update_cookie.call_count == 1:
                raise TimeoutError('runtime restart failed')
            manager.cookies[cid] = value

        manager.update_cookie = Mock(side_effect=restart)
        guard = Mock()
        service = DesktopBridgeService(
            self.db,
            lambda cid, value, user: reply_server.import_account_session(
                cid, reply_server.SessionImportRequest(value=value), user),
            lambda: self.now,
        )
        token = service.pair('seller', 1)['token']
        with (patch('app.db_manager.db_manager', self.db),
              patch.object(reply_server.cookie_manager, 'manager', manager),
              patch('utils.risk_control.registry.get', return_value=guard)):
            with self.assertRaises(HTTPException):
                service.sync(token, 'unb=123; _m_h5_tk=new')
            guard.reset.assert_not_called()
            self.assertTrue(service.sync(token, 'unb=123; _m_h5_tk=new')['changed'])
            self.assertEqual(manager.cookies['seller'], self.db.get_cookie('seller'))
            self.assertEqual(manager.update_cookie.call_count, 2)
            guard.reset.assert_called_once()

    def test_pair_rotation_waits_for_active_sync_without_holding_db_lock(self):
        entered = threading.Event()
        release = threading.Event()
        rotated = threading.Event()
        errors = []

        def importer(cid, value, user):
            # Simulate CookieManager's separate asyncio thread reading DB.
            reader = threading.Thread(target=lambda: (self.db.get_cookie(cid), entered.set()))
            reader.start()
            if not entered.wait(2):
                raise RuntimeError('DB lock was held during callback')
            release.wait(2)
            reader.join()
            self.save(cid, value, user)

        service = DesktopBridgeService(self.db, importer, lambda: self.now)
        token = service.pair('seller', 1)['token']

        def sync():
            try:
                service.sync(token, 'unb=123; _m_h5_tk=new')
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=sync)
        thread.start()
        self.assertTrue(entered.wait(2))
        rotate = threading.Thread(target=lambda: (service.pair('seller', 1), rotated.set()))
        rotate.start()
        self.assertFalse(rotated.wait(.05))
        release.set()
        thread.join(3)
        rotate.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(rotate.is_alive())
        self.assertFalse(errors)
        with self.assertRaises(HTTPException) as error:
            service.sync(token, 'unb=123; _m_h5_tk=new')
        self.assertEqual(error.exception.status_code, 401)


if __name__ == '__main__':
    unittest.main()
