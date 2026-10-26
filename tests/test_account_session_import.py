import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app.reply_server import import_account_session, SessionImportRequest


class AccountSessionImportTests(unittest.TestCase):
    def setUp(self):
        self.owner = {'user_id': 1}

    def test_matching_session_is_updated_and_guard_reset(self):
        guard = Mock()
        with (
            patch('app.db_manager.db_manager.get_all_cookies', return_value={'seller': 'unb=123; _m_h5_tk=old'}),
            patch('app.reply_server.update_cookie') as update,
            patch('utils.risk_control.registry.get', return_value=guard),
        ):
            result = import_account_session('seller', SessionImportRequest(value='Cookie: unb=123;_m_h5_tk=new'), self.owner)
        self.assertTrue(result['success'])
        self.assertEqual(update.call_args.args[1].value, 'unb=123; _m_h5_tk=new')
        guard.reset.assert_called_once()

    def test_wrong_account_missing_fields_and_headers_do_not_update(self):
        with (
            patch('app.db_manager.db_manager.get_all_cookies', return_value={'seller': 'unb=123; _m_h5_tk=old'}),
            patch('app.reply_server.update_cookie') as update,
        ):
            for value in ('unb=456; _m_h5_tk=new', 'unb=123', 'unb=123; _m_h5_tk=new\nOther: x'):
                with self.assertRaises(HTTPException) as error:
                    import_account_session('seller', SessionImportRequest(value=value), self.owner)
                self.assertEqual(error.exception.status_code, 400)
        update.assert_not_called()

    def test_foreign_owner_is_rejected(self):
        with patch('app.db_manager.db_manager.get_all_cookies', return_value={}):
            with self.assertRaises(HTTPException) as error:
                import_account_session('seller', SessionImportRequest(value='unb=123; _m_h5_tk=new'), self.owner)
        self.assertEqual(error.exception.status_code, 403)
