import tempfile
import unittest
from app.db_manager import DBManager


class LifetimeSalesTests(unittest.TestCase):
    def test_total_uses_all_dates_valid_statuses_and_current_user(self):
        with tempfile.TemporaryDirectory() as path:
            db = DBManager(path + '/sales.db')
            db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('mine','unb=1',1)")
            db.conn.execute("INSERT INTO cookies(id,value,user_id) VALUES('theirs','unb=2',2)")
            for order in [
                ('old','mine','completed','100.25','2020-01-01'),
                ('new','mine','pending_ship','35.50','2026-10-01'),
                ('cancelled','mine','cancelled','999','2026-10-01'),
                ('refund','mine','refunding','999','2026-10-01'),
                ('other','theirs','completed','999','2026-10-01'),
            ]:
                db.conn.execute('INSERT INTO orders(order_id,cookie_id,order_status,amount,created_at) VALUES(?,?,?,?,?)', order)
            db.conn.commit()
            statuses = ['pending_ship', 'shipped', 'completed']
            total = db.get_order_analytics(user_id=1, include_statuses=statuses)['revenue_stats']
            period = db.get_order_analytics(start_date='2026-10-01', end_date='2026-10-31', user_id=1, include_statuses=statuses)['revenue_stats']
            self.assertEqual(total['total_amount'], 135.75)
            self.assertEqual(period['total_amount'], 35.50)
            db.close()
