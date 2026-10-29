import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.db_manager import DBManager
from app.ai_reply_engine import AIReplyEngine


class SystemAIPolicyTests(unittest.TestCase):
    def test_global_connection_takes_priority_and_legacy_key_survives(self):
        with tempfile.TemporaryDirectory() as root:
            db = DBManager(root + '/test.db')
            db.save_ai_reply_settings('a', {'ai_enabled': True, 'model_name': 'old-model', 'api_key': 'legacy', 'base_url': 'https://old.example'})
            self.assertEqual(db.get_ai_reply_settings('a')['api_key'], 'legacy')
            for key, value in [('ai_api_key', 'new-key'), ('ai_api_url', 'https://new.example'), ('ai_model', 'new-model')]:
                db.set_system_setting(key, value)
            settings = db.get_ai_reply_settings('a')
            self.assertEqual((settings['model_name'], settings['api_key'], settings['base_url']), ('new-model', 'new-key', 'https://new.example'))
            db.close()

    def test_openai_reasoning_parameters_and_default(self):
        engine = AIReplyEngine()
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content='ok'))]
        settings = {'model_name': 'qwen-plus', 'base_url': 'https://example.test'}
        with patch('app.ai_reply_engine.db_manager.get_system_setting', return_value='off'):
            engine._call_openai_api(client, settings, [])
        self.assertEqual(client.chat.completions.create.call_args.kwargs['extra_body'], {'enable_thinking': False})
        with patch('app.ai_reply_engine.db_manager.get_system_setting', return_value='high'):
            engine._call_openai_api(client, settings, [])
        self.assertEqual(client.chat.completions.create.call_args.kwargs['extra_body']['thinking_budget'], 8192)
        with patch('app.ai_reply_engine.db_manager.get_system_setting', return_value='default'):
            engine._call_openai_api(client, settings, [])
        self.assertNotIn('extra_body', client.chat.completions.create.call_args.kwargs)
