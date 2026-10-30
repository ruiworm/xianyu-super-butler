import tempfile
import unittest
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.db_manager import DBManager
from app.item_settlement import create_settlement_router
from app.settlement_math import estimate_settlement

class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DBManager(self.temp.name+'/db.sqlite')
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('mine','unb=1',1)")
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('theirs','unb=2',2)")
        self.db.conn.execute("INSERT INTO item_info(cookie_id,item_id,item_title) VALUES('mine','a','A')")
        self.db.conn.execute("INSERT INTO item_info(cookie_id,item_id,item_title) VALUES('mine','b','B')")
        self.db.conn.commit()
        app = FastAPI()
        app.include_router(create_settlement_router(lambda: {'user_id': 1}, self.db))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.db.close(); self.temp.cleanup()

    def test_decimal_calculation_and_explicit_worry_free_base(self):
        self.assertEqual(estimate_settlement('100', 1, ('percent',None,'20','0','')), Decimal('78.40'))
        self.assertEqual(estimate_settlement('100', 1, ('percent',None,'20','10','parallel')), Decimal('68.40'))
        self.assertEqual(estimate_settlement('100', 1, ('percent',None,'20','10','sequential')), Decimal('70.40'))
        self.assertEqual(estimate_settlement('100', 1, ('percent',None,'0','0','')), Decimal('98.40'))
        self.assertIsNone(estimate_settlement('100', 1, ('percent',None,'100','0','')))
        self.assertEqual(estimate_settlement('123', 3, ('fixed','12.35','0','0','')), Decimal('37.05'))
        self.assertEqual(estimate_settlement('0.05', 1, ('percent',None,'30','0','')), Decimal('0.03'))
        self.assertIsNone(estimate_settlement('100', 1, ('percent',None,'20','10','')))
        self.assertIsNone(estimate_settlement('NaN', 1, ('percent',None,'20','0','')))
        self.assertIsNone(estimate_settlement('100', 1, None))

    def test_api_scope_validation_and_aggregate_excludes_unknowns(self):
        url='/item-settlement/mine/a'
        self.assertEqual(self.client.get(url).json(), {'configured': False})
        self.assertEqual(self.client.put(url,json={'mode':'percent','coin_rate':'20','worry_rate':'10'}).status_code,422)
        self.assertEqual(self.client.put('/item-settlement/theirs/a',json={'mode':'fixed','fixed_unit':'10'}).status_code,403)
        self.assertEqual(self.client.put(url,json={'mode':'percent','coin_rate':'20','worry_rate':'0'}).status_code,200)
        self.assertEqual(self.client.get(url).json()['coin_rate'],'20')
        for row in [('a1','mine','a','pending_ship','100',1),('a2','mine','a','completed','50',1),
                    ('unknown','mine','b','completed','200',1),('refund','mine','a','refunding','1000',1),
                    ('other','theirs','a','completed','1000',1)]:
            self.db.conn.execute('INSERT INTO orders(order_id,cookie_id,item_id,order_status,amount,buy_num) VALUES(?,?,?,?,?,?)',row)
        self.db.conn.commit()
        stats=self.db.get_order_analytics(user_id=1,include_statuses=['pending_ship','shipped','completed'])['revenue_stats']
        self.assertEqual(stats['estimated_settlement'],117.6)
        self.assertEqual(stats['estimated_settlement_orders'],2)
        self.assertEqual(stats['unconfigured_settlement_orders'],1)
        backup=self.db.export_backup(user_id=1)
        self.assertTrue(self.db.import_backup(backup,user_id=1))
        self.assertTrue(self.client.get(url).json()['configured'])
        self.assertEqual(self.client.delete(url).status_code,200)
        self.assertFalse(self.client.get(url).json()['configured'])
