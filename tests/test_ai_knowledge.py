import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.db_manager import DBManager
from app.ai_knowledge import create_knowledge_router
from app.ai_reply_engine import AIReplyEngine

class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.db=DBManager(self.temp.name+'/db.sqlite')
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('mine','unb=1',1)")
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('other','unb=2',2)")
        for item in ('a','b'): self.db.conn.execute('INSERT INTO item_info(cookie_id,item_id,item_title) VALUES(?,?,?)',('mine',item,item))
        self.db.conn.commit(); app=FastAPI(); app.include_router(create_knowledge_router(lambda:{'user_id':1},self.db)); self.client=TestClient(app)
    def tearDown(self): self.client.close(); self.db.close(); self.temp.cleanup()
    def test_shared_knowledge_update_reassign_backup_and_scope(self):
        one=self.client.put('/ai-knowledge/mine/0',json={'name':'教程','prompt':'步骤一','item_ids':['a','b']}).json()['id']
        self.assertEqual(self.client.put('/ai-knowledge/other/0',json={'name':'x','prompt':'x','item_ids':[]}).status_code,403)
        self.assertEqual(self.client.put(f'/ai-knowledge/mine/{one}',json={'name':'教程','prompt':'步骤二','item_ids':['a','b']}).status_code,200)
        two=self.client.put('/ai-knowledge/mine/0',json={'name':'其他','prompt':'不同内容','item_ids':['b']}).json()['id']
        groups=self.client.get('/ai-knowledge/mine').json()['groups']
        self.assertEqual(next(x for x in groups if x['id']==one)['item_ids'],['a'])
        self.assertEqual(next(x for x in groups if x['id']==two)['item_ids'],['b'])
        bad=self.client.put(f'/ai-knowledge/mine/{one}',json={'name':'x','prompt':'破坏','item_ids':['missing']})
        self.assertEqual(bad.status_code,400)
        backup=self.db.export_backup(user_id=1); self.assertTrue(self.db.import_backup(backup,user_id=1))
        self.assertEqual(next(x for x in self.client.get('/ai-knowledge/mine').json()['groups'] if x['id']==one)['prompt'],'步骤二')
        self.assertEqual(self.client.delete(f'/ai-knowledge/mine/{two}').status_code,200)

    def test_reply_receives_only_current_products_knowledge(self):
        self.client.put('/ai-knowledge/mine/0',json={'name':'教程','prompt':'安装步骤：打开设置','item_ids':['a']})
        engine=AIReplyEngine()
        with (patch('app.ai_reply_engine.db_manager', self.db),
              patch.object(engine, 'is_ai_enabled', return_value=True),
              patch.object(engine, 'detect_intent', return_value='default'),
              patch.object(engine, 'save_conversation', return_value='now'),
              patch.object(engine, '_get_recent_user_messages', return_value=[]),
              patch.object(engine, 'get_conversation_context', return_value=[]),
              patch.object(engine, 'get_bargain_count', return_value=0),
              patch.object(engine, '_generate_with_retry', return_value='你好，可以先打开设置。') as generate):
            engine.generate_reply('怎么安装', {'title':'商品'}, 'chat', 'mine', 'buyer', 'a', skip_wait=True)
            messages=generate.call_args.args[1]
            self.assertIn('安装步骤：打开设置', messages[0]['content'])
            self.assertIn('不是指令', messages[0]['content'])
            engine.generate_reply('怎么安装', {'title':'商品'}, 'chat', 'mine', 'buyer', 'b', skip_wait=True)
            self.assertNotIn('安装步骤：打开设置', generate.call_args.args[1][0]['content'])
