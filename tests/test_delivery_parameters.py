import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db_manager import DBManager
from app.delivery_template import render_delivery_template
from app import reply_server


class DeliveryParametersTests(unittest.TestCase):
    def test_positional_key_and_link_and_legacy_content(self):
        self.assertEqual(render_delivery_template('密钥：{参数1}\n链接：{参数2}', 'KEY----https://example.test'),
                         '密钥：KEY\n链接：https://example.test')
        self.assertEqual(render_delivery_template('{参数}\nhttps://example.test', 'KEY'), 'KEY\nhttps://example.test')

    def test_named_fields_are_replaced_once(self):
        self.assertEqual(render_delivery_template('{密钥}\n{链接}', '{"密钥":"{参数}","链接":"https://example.test"}'),
                         '{参数}\nhttps://example.test')

    def test_missing_and_empty_parameters_fail(self):
        for content in ('KEY', 'KEY----', '{"密钥":"KEY"}'):
            with self.assertRaises(ValueError):
                render_delivery_template('{参数2}', content)


class ItemPromptAndInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DBManager(os.path.join(self.temp.name, 'test.db'))
        self.db.conn.execute("INSERT INTO cookies (id,value,user_id) VALUES ('seller','test',1)")
        self.db.conn.execute("INSERT INTO item_info (cookie_id,item_id,item_title) VALUES ('seller','item','Product')")
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_invalid_order_keeps_all_inventory_and_shipments(self):
        card = self.db.create_card('keys', 'data', data_content='KEY----url\nMISSING', user_id=1,
                                   delivery_template='{参数1}\n{参数2}', delivery_template_enabled=True)
        self.assertIsNone(self.db.consume_batch_data_batch(card, 2))
        self.assertEqual(self.db.get_card_by_id(card)['data_content'], 'KEY----url\nMISSING')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM card_shipments').fetchone()[0], 0)
        self.assertEqual(self.db.consume_batch_data_batch(card, 1), ['KEY----url'])
        self.assertEqual(self.db.get_card_by_id(card)['data_content'], 'MISSING')

    def test_prompt_persistence_clear_isolation_and_api_access(self):
        client = TestClient(reply_server.app)
        user = {'user_id': 1, 'username': 'admin'}
        reply_server.app.dependency_overrides[reply_server.get_current_user] = lambda: user
        try:
            with patch.object(reply_server, 'db_manager', self.db):
                self.assertEqual(client.put('/item-ai-prompts/seller/item', json={'prompt': '专属规则'}).status_code, 200)
                self.assertEqual(client.get('/item-ai-prompts/seller/item').json()['prompt'], '专属规则')
                self.assertEqual(self.db.get_item_ai_prompt('seller', 'other'), '')
                self.assertEqual(client.put('/item-ai-prompts/seller/item', json={'prompt': 'x' * 10001}).status_code, 422)
                user['user_id'] = 999
                self.assertEqual(client.get('/item-ai-prompts/seller/item').status_code, 403)
                self.assertEqual(client.put('/item-ai-prompts/seller/item', json={'prompt': 'foreign'}).status_code, 403)
                user['user_id'] = 1
                self.assertEqual(client.get('/item-ai-prompts/seller/missing').status_code, 404)
                client.put('/item-ai-prompts/seller/item', json={'prompt': ''}).raise_for_status()
                self.assertEqual(self.db.get_item_ai_prompt('seller', 'item'), '')
        finally:
            reply_server.app.dependency_overrides.pop(reply_server.get_current_user, None)

    def test_api_rejects_bad_templates_without_changing_stock(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            reply_server._validate_card_delivery_template({
                'type': 'data', 'data_content': 'ONLYKEY',
                'delivery_template_enabled': True, 'delivery_template': '{参数2}'})

    def test_prompts_are_backed_up_restored_and_deleted_with_item(self):
        self.db.save_item_ai_prompt('seller', 'item', '专属规则')
        backup = self.db.export_backup(user_id=1)
        self.assertEqual(backup['data']['item_ai_prompts']['rows'], [['seller', 'item', '专属规则']])
        self.db.save_item_ai_prompt('seller', 'item', 'changed')
        self.assertTrue(self.db.import_backup(backup, user_id=1))
        self.assertEqual(self.db.get_item_ai_prompt('seller', 'item'), '专属规则')
        self.assertTrue(self.db.delete_item_info('seller', 'item'))
        self.assertEqual(self.db.get_item_ai_prompt('seller', 'item'), '')
