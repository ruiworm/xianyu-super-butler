"""Seller-defined product settlement estimates."""
from decimal import Decimal
from typing import Dict, Any, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

class SettlementRule(BaseModel):
    mode: Literal['fixed', 'percent']
    fixed_unit: Optional[Decimal] = Field(default=None, ge=0, le=10000000, allow_inf_nan=False)
    coin_rate: Decimal = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    worry_rate: Decimal = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    fee_order: Literal['parallel', 'sequential', ''] = ''

    @model_validator(mode='after')
    def validate_rule(self):
        if self.mode == 'fixed' and self.fixed_unit is None:
            raise ValueError('请填写每件到手金额')
        if self.mode == 'percent' and self.worry_rate > 0 and not self.fee_order:
            raise ValueError('请选择无忧卖费用的计算方式')
        if self.mode == 'percent' and self.fee_order == 'parallel' and self.coin_rate + self.worry_rate > 100:
            raise ValueError('并列扣费比例合计不能超过100%')
        return self

def create_settlement_router(auth, db):
    router = APIRouter()
    def check(cookie_id, item_id, user):
        if cookie_id not in db.get_all_cookies(user['user_id']):
            raise HTTPException(403, '无权限操作该账号')
        if not db.get_item_info(cookie_id, item_id):
            raise HTTPException(404, '商品不存在')

    @router.get('/item-settlement/{cookie_id}/{item_id}')
    def get_rule(cookie_id: str, item_id: str, user: Dict[str, Any] = Depends(auth)):
        check(cookie_id, item_id, user)
        with db.lock:
            row = db.conn.execute('''SELECT mode,fixed_unit,coin_rate,worry_rate,fee_order
                FROM item_settlement_rules WHERE cookie_id=? AND item_id=?''', (cookie_id,item_id)).fetchone()
        return {'configured': False} if not row else {
            'configured': True, **dict(zip(('mode','fixed_unit','coin_rate','worry_rate','fee_order'), row))}

    @router.put('/item-settlement/{cookie_id}/{item_id}')
    def put_rule(cookie_id: str, item_id: str, data: SettlementRule, user: Dict[str, Any] = Depends(auth)):
        check(cookie_id, item_id, user)
        with db.lock:
            db.conn.execute('''INSERT INTO item_settlement_rules(cookie_id,item_id,mode,fixed_unit,coin_rate,worry_rate,fee_order)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(cookie_id,item_id) DO UPDATE SET
                mode=excluded.mode,fixed_unit=excluded.fixed_unit,coin_rate=excluded.coin_rate,
                worry_rate=excluded.worry_rate,fee_order=excluded.fee_order''',
                (cookie_id,item_id,data.mode,str(data.fixed_unit) if data.fixed_unit is not None else None,
                 str(data.coin_rate),str(data.worry_rate),data.fee_order))
            db.conn.commit()
        return {'success': True}

    @router.delete('/item-settlement/{cookie_id}/{item_id}')
    def delete_rule(cookie_id: str, item_id: str, user: Dict[str, Any] = Depends(auth)):
        check(cookie_id,item_id,user)
        with db.lock:
            db.conn.execute('DELETE FROM item_settlement_rules WHERE cookie_id=? AND item_id=?', (cookie_id,item_id))
            db.conn.commit()
        return {'success': True}
    return router
