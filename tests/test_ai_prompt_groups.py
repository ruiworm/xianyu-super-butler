import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.db_manager import DBManager
import app.reply_server as server

class PromptGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DBManager(self.temp.name + '/test.db')
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('mine','unb=1',1)")
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('other','unb=2',2)")
        for account, item in [('mine','a'),('mine','b'),('mine','c'),('other','x')]:
            self.db.conn.execute('INSERT INTO item_info(cookie_id,item_id,item_title) VALUES(?,?,?)', (account,item,item))
        self.db.conn.commit()
        self.patcher=patch.object(server, 'db_manager', self.db)
        self.patcher.start()
        self.user={'user_id':1}

    def tearDown(self):
        self.patcher.stop(); self.db.close(); self.temp.cleanup()

    def save(self, group_id, name, prompt, items):
        return server.save_ai_prompt_group('mine', group_id, server.AIPromptGroupRequest(name=name,prompt=prompt,item_ids=items), self.user)['id']

    def test_one_prompt_many_products_reassign_edit_and_delete(self):
        one=self.save(0,'售后','共同说明',['a','b'])
        self.assertEqual(self.db.get_item_ai_prompt('mine','a'), '共同说明')
        self.assertEqual(self.db.get_item_ai_prompt('mine','b'), '共同说明')
        self.save(one,'售后','新说明',['a','b'])
        self.assertEqual(self.db.get_item_ai_prompt('mine','b'), '新说明')
        two=self.save(0,'教程','另一说明',['b','c'])
        groups=server.list_ai_prompt_groups('mine',self.user)['groups']
        self.assertEqual(next(x for x in groups if x['id']==one)['item_ids'],['a'])
        self.assertEqual(self.db.get_item_ai_prompt('mine','b'), '另一说明')
        server.delete_ai_prompt_group('mine',two,self.user)
        self.assertEqual(self.db.get_item_ai_prompt('mine','b'), '')
        self.assertEqual(self.db.get_item_ai_prompt('mine','a'), '新说明')

    def test_migration_and_failed_save_are_safe(self):
        self.db.save_item_ai_prompt('mine','a','旧规则')
        self.db.save_item_ai_prompt('mine','b','旧规则')
        group=server.list_ai_prompt_groups('mine',self.user)['groups'][0]
        self.assertEqual(group['item_ids'],['a','b'])
        with self.assertRaises(HTTPException): self.save(group['id'],'改名','坏数据',['x'])
        self.assertEqual(self.db.get_item_ai_prompt('mine','a'),'旧规则')
        self.assertEqual(server.list_ai_prompt_groups('mine',self.user)['groups'][0]['name'],group['name'])
        with self.assertRaises(HTTPException): server.list_ai_prompt_groups('other',self.user)

    def test_backup_restore_and_product_delete(self):
        self.save(0,'方案','规则',['a','b'])
        backup=self.db.export_backup(user_id=1)
        self.assertTrue(self.db.import_backup(backup,user_id=1))
        self.assertEqual(self.db.get_item_ai_prompt('mine','a'),'规则')
        self.assertTrue(self.db.delete_item_info('mine','a'))
        self.assertEqual(server.list_ai_prompt_groups('mine',self.user)['groups'][0]['item_ids'],['b'])
