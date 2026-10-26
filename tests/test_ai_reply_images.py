import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db_manager import DBManager
from app.ai_reply_images import create_ai_reply_images_router, load_rules, select_reply_image
from app.ai_reply_engine import AIReplyEngine


class ReplyImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = DBManager(str(self.root / 'images.db'))
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('seller','unb=123',1)")
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('other','unb=456',1)")
        self.db.conn.commit()
        image = self.root / 'static/uploads/images/sample.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'fixture')
        self.root_patch = patch('app.ai_reply_images.ROOT', self.root)
        self.root_patch.start()
        self.user = {'user_id': 1}
        app = FastAPI()
        app.include_router(create_ai_reply_images_router(lambda: self.user, self.db))
        self.client = TestClient(app)
        self.rule = dict(id='install', title='安装图', intent='买家询问如何安装时发送',
                         image_url='/static/uploads/images/sample.png', item_id='', enabled=True)

    def tearDown(self):
        self.client.close()
        self.root_patch.stop()
        self.db.close()
        self.temp.cleanup()

    def save(self, rules):
        return self.client.put('/ai-reply-images/seller', json={'rules': rules})

    def choose(self, value, item_id='item'):
        generate = Mock(return_value=value)
        result = select_reply_image(self.db, generate, {}, 'seller', item_id,
                                    '这个要怎么装？', [], {'title': '测试商品'})
        return result, generate

    def test_owner_validation_and_only_existing_uploaded_paths(self):
        self.assertEqual(self.save([self.rule]).status_code, 200)
        self.assertEqual(self.save([{**self.rule, 'image_url': 'static/uploads/images/sample.png'}]).status_code, 200)
        self.assertEqual(load_rules(self.db, 'seller')[0]['image_url'], self.rule['image_url'])
        self.assertEqual(self.client.get('/ai-reply-images/other').json()['rules'], [])
        for url in ('https://example.test/a.png', '/static/uploads/images/../secret.png', '/etc/passwd'):
            self.assertEqual(self.save([{**self.rule, 'image_url': url}]).status_code, 422)
        self.assertEqual(self.save([{**self.rule, 'image_url': '/static/uploads/images/missing.png'}]).status_code, 400)
        self.assertEqual(self.save([self.rule, self.rule]).status_code, 422)
        self.user = {'user_id': 999}
        self.assertEqual(self.save([]).status_code, 403)
        self.assertEqual(self.client.get('/ai-reply-images/seller').status_code, 403)

    def test_unconfigured_disabled_wrong_product_and_missing_file_do_not_call_ai(self):
        for rules in ([], [{**self.rule, 'enabled': False}], [{**self.rule, 'item_id': 'different'}]):
            self.save(rules).raise_for_status()
            result, generate = self.choose('{"image_id":"install","confidence":1}')
            self.assertIsNone(result)
            generate.assert_not_called()
        self.save([self.rule]).raise_for_status()
        (self.root / self.rule['image_url'].lstrip('/')).unlink()
        result, generate = self.choose('{"image_id":"install","confidence":1}')
        self.assertIsNone(result)
        generate.assert_not_called()

    def test_semantic_choice_resolves_known_id_and_rejects_untrusted_outputs(self):
        self.save([self.rule]).raise_for_status()
        result, generate = self.choose('{"image_id":"install","confidence":0.95}')
        self.assertEqual(result['image_url'], self.rule['image_url'])
        messages = generate.call_args.args[1]
        self.assertNotIn(self.rule['image_url'], json.dumps(messages))
        for raw in ('nonsense', '[]', 'null', '{"image_id":null,"confidence":1}',
                    '{"image_id":"install","confidence":0.5}',
                    '{"image_id":"install","confidence":true}',
                    '{"image_id":"install","confidence":NaN}',
                    '{"image_id":"https://evil.test/image.png","confidence":1}'):
            self.assertIsNone(self.choose(raw)[0])

    def test_product_specific_image_takes_precedence_and_backup_restores_rules(self):
        scoped = {**self.rule, 'id': 'scoped', 'item_id': 'item'}
        self.save([self.rule, scoped]).raise_for_status()
        self.assertIsNone(self.choose('{"image_id":"install","confidence":1}')[0])
        self.assertEqual(self.choose('{"image_id":"scoped","confidence":1}')[0]['id'], 'scoped')
        backup = self.db.export_backup(user_id=1)
        self.save([])
        self.assertTrue(self.db.import_backup(backup, user_id=1))
        self.assertEqual(len(load_rules(self.db, 'seller')), 2)
        self.db.delete_cookie('seller')
        self.assertEqual(load_rules(self.db, 'seller'), [])

    def test_ai_pipeline_returns_image_marker_only_for_selected_saved_rule(self):
        self.save([self.rule]).raise_for_status()
        engine = AIReplyEngine()
        with (patch('app.ai_reply_engine.db_manager', self.db),
              patch.object(engine, 'is_ai_enabled', return_value=True),
              patch.object(engine, 'detect_intent', return_value='default'),
              patch.object(engine, 'save_conversation', return_value='now'),
              patch.object(engine, '_get_recent_user_messages', return_value=[]),
              patch.object(engine, 'get_conversation_context', return_value=[]),
              patch.object(engine, 'get_bargain_count', return_value=0),
              patch.object(engine, '_generate_with_retry', return_value='{"image_id":"install","confidence":0.95}') as generate):
            reply = engine.generate_reply('这个怎么装？', {'title': '配件'}, 'chat', 'seller', 'buyer', 'item', skip_wait=True)
        self.assertEqual(reply, '__IMAGE_SEND__' + self.rule['image_url'])
        generate.assert_called_once()

    def test_uncertain_selection_falls_back_to_text_reply(self):
        self.save([self.rule]).raise_for_status()
        engine = AIReplyEngine()
        with (patch('app.ai_reply_engine.db_manager', self.db),
              patch.object(engine, 'is_ai_enabled', return_value=True),
              patch.object(engine, 'detect_intent', return_value='default'),
              patch.object(engine, 'save_conversation', return_value='now'),
              patch.object(engine, '_get_recent_user_messages', return_value=[]),
              patch.object(engine, 'get_conversation_context', return_value=[]),
              patch.object(engine, 'get_bargain_count', return_value=0),
              patch.object(engine, '_generate_with_retry', side_effect=['{"image_id":null,"confidence":0}', '你好，请问有什么需要？'])):
            reply = engine.generate_reply('你好', {'title': '配件'}, 'chat', 'seller', 'buyer', 'item', skip_wait=True)
        self.assertEqual(reply, '你好，请问有什么需要？')


class ReplyImageSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_photo_is_uploaded_and_sent_as_an_image_message(self):
        from XianyuAutoAsync import XianyuLive
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = 'seller'
        live.myid = '123'
        live.cookies_str = 'unb=123; _m_h5_tk=test'
        websocket = AsyncMock()
        uploader = MagicMock()
        uploader.__aenter__ = AsyncMock(return_value=uploader)
        uploader.__aexit__ = AsyncMock(return_value=False)
        uploader.upload_image = AsyncMock(return_value='https://img.alicdn.com/example.jpg')
        with (patch('utils.image_uploader.ImageUploader', return_value=uploader),
              patch('os.path.exists', return_value=True),
              patch('utils.image_utils.image_manager.get_image_size', return_value=(640, 480))):
            await live.send_image_msg(websocket, 'chat', 'buyer', '/static/uploads/images/sample.png')
        uploader.upload_image.assert_awaited_once_with('static/uploads/images/sample.png')
        sent = json.loads(websocket.send.call_args.args[0])
        content = json.loads(base64.b64decode(sent['body'][0]['content']['custom']['data']))
        self.assertEqual(content['contentType'], 2)
        self.assertEqual(content['image']['pics'][0]['url'], 'https://img.alicdn.com/example.jpg')
        self.assertEqual(content['image']['pics'][0]['width'], 640)


if __name__ == '__main__':
    unittest.main()
