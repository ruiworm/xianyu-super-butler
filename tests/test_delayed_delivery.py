import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from app.db_manager import DBManager
from app.delayed_delivery import DelayedDeliveryStore
from XianyuAutoAsync import XianyuLive

class DelayedDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.db=DBManager(self.temp.name+'/db.sqlite')
        self.db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('seller','unb=1',1)")
        self.db.conn.execute("INSERT INTO item_info(cookie_id,item_id,item_title,item_detail) VALUES('seller','item','商品','详情')")
        self.db.conn.commit()
        self.card=self.db.create_card('库存','text',text_content='测试内容',user_id=1,delay_seconds=60)
        self.db.save_item_delivery_config(user_id=1,cookie_id='seller',item_id='item',enabled=True,is_multi_spec=False,variants=[{'card_id':self.card,'delivery_count':1}])
        self.live=object.__new__(XianyuLive); self.live.cookie_id='seller'; self.live.ws=Mock()
        self.live.save_item_info_to_db=AsyncMock(); self.live.fetch_order_detail_info=AsyncMock(return_value={'order_status':'pending_ship'})
        self.meta={'_delay_meta':{'buyer_name':'买家','chat_id':'chat'}}
    async def asyncTearDown(self): self.db.close(); self.temp.cleanup()

    async def test_interrupted_wait_recovers_and_claims_only_once(self):
        with patch('app.db_manager.db_manager',self.db), patch('XianyuAutoAsync.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.live._auto_delivery('item','商品','order','buyer',delivery_context=self.meta)
        store=DelayedDeliveryStore(self.db)
        self.assertEqual(store.get('seller','order')['state'],'waiting')
        self.db.conn.execute('UPDATE delayed_delivery_jobs SET due_at=0 WHERE order_id=?',('order',));self.db.conn.commit()
        with patch('app.db_manager.db_manager',self.db), patch('XianyuAutoAsync.asyncio.sleep',new=AsyncMock()):
            value=await self.live._auto_delivery('item','商品','order','buyer',delivery_context=self.meta)
            self.assertEqual(value,'测试内容')
            self.assertEqual(store.get('seller','order')['state'],'processing')
            duplicate=await self.live._auto_delivery('item','商品','order','buyer',delivery_context={'_delay_meta':{'buyer_name':'买家','chat_id':'chat'}})
            self.assertIsNone(duplicate)
        self.assertTrue(store.transition('seller','order','processing','review'))
        self.assertFalse(store.claim('seller','order'))
        self.assertEqual(store.get('seller','order')['state'],'review')

    async def test_cancelled_order_does_not_take_stock(self):
        store=DelayedDeliveryStore(self.db,clock=lambda:0)
        store.schedule('seller','order','item','buyer','买家','chat',1)
        self.live.fetch_order_detail_info=AsyncMock(return_value={'order_status':'cancelled'})
        with patch('app.db_manager.db_manager',self.db), patch('XianyuAutoAsync.asyncio.sleep',new=AsyncMock()):
            result=await self.live._auto_delivery('item','商品','order','buyer',delivery_context=self.meta)
        self.assertIsNone(result)
        self.assertEqual(store.get('seller','order')['state'],'cancelled')

    async def test_restored_waiting_job_requires_manual_review(self):
        store = DelayedDeliveryStore(self.db, clock=lambda: 0)
        store.schedule('seller', 'order', 'item', 'buyer', '买家', 'chat', 1)
        backup = self.db.export_backup(user_id=1)
        self.assertTrue(self.db.import_backup(backup, user_id=1))
        self.assertEqual(store.get('seller', 'order')['state'], 'review')
        self.assertFalse(store.claim('seller', 'order'))

    async def test_transient_status_remains_waiting(self):
        store = DelayedDeliveryStore(self.db, clock=lambda: 0)
        store.schedule('seller', 'order', 'item', 'buyer', '买家', 'chat', 1)
        self.live.fetch_order_detail_info = AsyncMock(return_value={'order_status': 'processing'})
        with patch('app.db_manager.db_manager', self.db), patch('XianyuAutoAsync.asyncio.sleep', new=AsyncMock()):
            result = await self.live._auto_delivery('item', '商品', 'order', 'buyer', delivery_context=self.meta)
        self.assertIsNone(result)
        self.assertEqual(store.get('seller', 'order')['state'], 'waiting')

    async def test_recovery_only_replays_waiting_due_jobs(self):
        store=DelayedDeliveryStore(self.db,clock=lambda:0)
        store.schedule('seller','due','item','buyer','买家','chat',1)
        store.schedule('seller','claimed','item','buyer','买家','chat',1)
        self.db.conn.execute("UPDATE delayed_delivery_jobs SET due_at=0 WHERE cookie_id='seller'");self.db.conn.commit()
        self.assertTrue(store.claim('seller','claimed'))
        self.live._handle_auto_delivery=AsyncMock()
        with patch('app.db_manager.db_manager',self.db), patch('XianyuAutoAsync.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.live._recover_delayed_deliveries(self.live.ws)
        self.live._handle_auto_delivery.assert_awaited_once()
        self.assertEqual(self.live._handle_auto_delivery.await_args.kwargs['recovery_order_id'], 'due')
        self.assertEqual(store.get('seller','claimed')['state'], 'processing')
