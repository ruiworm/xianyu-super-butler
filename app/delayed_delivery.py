"""Persistent delayed-delivery state. Never automatically retry a claimed job."""
import time

class DelayedDeliveryStore:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    def get(self, cookie_id, order_id):
        with self.db.lock:
            row = self.db.conn.execute('''SELECT cookie_id,order_id,item_id,buyer_id,buyer_name,chat_id,due_at,state
                FROM delayed_delivery_jobs WHERE cookie_id=? AND order_id=?''', (cookie_id,order_id)).fetchone()
        return dict(zip(('cookie_id','order_id','item_id','buyer_id','buyer_name','chat_id','due_at','state'),row)) if row else None

    def schedule(self, cookie_id, order_id, item_id, buyer_id, buyer_name, chat_id, seconds):
        if not all((cookie_id, order_id, item_id, buyer_id, chat_id)):
            raise ValueError('delayed delivery requires account, order, item, buyer and chat')
        with self.db.lock:
            conn=self.db.conn
            conn.execute('''INSERT OR IGNORE INTO delayed_delivery_jobs
                (cookie_id,order_id,item_id,buyer_id,buyer_name,chat_id,due_at,state,updated_at)
                VALUES(?,?,?,?,?,?,?,'waiting',?)''',
                (cookie_id,order_id,item_id,buyer_id,buyer_name or '',chat_id,self.clock()+max(0,seconds),self.clock()))
            conn.commit()
            row=conn.execute('''SELECT cookie_id,order_id,item_id,buyer_id,buyer_name,chat_id,due_at,state
                FROM delayed_delivery_jobs WHERE cookie_id=? AND order_id=?''', (cookie_id,order_id)).fetchone()
        job=dict(zip(('cookie_id','order_id','item_id','buyer_id','buyer_name','chat_id','due_at','state'),row))
        if (job['item_id'],job['buyer_id'],job['chat_id']) != (item_id,buyer_id,chat_id):
            raise ValueError('existing delayed order identity does not match')
        return job

    def claim(self, cookie_id, order_id):
        with self.db.lock:
            cursor=self.db.conn.execute('''UPDATE delayed_delivery_jobs SET state='processing',updated_at=?
                WHERE cookie_id=? AND order_id=? AND state='waiting' AND due_at<=?''',
                (self.clock(),cookie_id,order_id,self.clock()))
            self.db.conn.commit()
            return cursor.rowcount == 1

    def transition(self, cookie_id, order_id, source, target):
        if (source,target) not in {('waiting','cancelled'),('waiting','completed'),('processing','completed'),('processing','review')}:
            raise ValueError('invalid delivery transition')
        with self.db.lock:
            cursor=self.db.conn.execute('''UPDATE delayed_delivery_jobs SET state=?,updated_at=?
                WHERE cookie_id=? AND order_id=? AND state=?''', (target,self.clock(),cookie_id,order_id,source))
            self.db.conn.commit()
            return cursor.rowcount == 1

    def due_jobs(self, cookie_id):
        with self.db.lock:
            rows=self.db.conn.execute('''SELECT cookie_id,order_id,item_id,buyer_id,buyer_name,chat_id,due_at,state
                FROM delayed_delivery_jobs WHERE cookie_id=? AND state='waiting' AND due_at<=? ORDER BY due_at LIMIT 50''',
                (cookie_id,self.clock())).fetchall()
        return [dict(zip(('cookie_id','order_id','item_id','buyer_id','buyer_name','chat_id','due_at','state'),row)) for row in rows]
