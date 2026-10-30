"""Account-scoped shared product knowledge; each product has at most one library."""
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

class KnowledgeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=10000)
    item_ids: List[str] = Field(default_factory=list, max_length=500)

def create_knowledge_router(get_current_user, db):
    router = APIRouter()

    def check(cookie_id, user):
        if cookie_id not in db.get_all_cookies(user['user_id']):
            raise HTTPException(403, '无权限操作该账号')

    @router.get('/ai-knowledge/{cookie_id}')
    def list_knowledge(cookie_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        check(cookie_id, user)
        with db.lock:
            rows = db.conn.execute('SELECT id,name,content FROM ai_knowledge_bases WHERE cookie_id=? ORDER BY id', (cookie_id,)).fetchall()
            links = db.conn.execute('SELECT knowledge_id,item_id FROM ai_knowledge_items WHERE cookie_id=? ORDER BY item_id', (cookie_id,)).fetchall()
        return {'groups': [{'id': row[0], 'name': row[1], 'prompt': row[2],
                            'item_ids': [link[1] for link in links if link[0] == row[0]]} for row in rows]}

    @router.put('/ai-knowledge/{cookie_id}/{group_id}')
    def save_knowledge(cookie_id: str, group_id: int, data: KnowledgeIn, user: Dict[str, Any] = Depends(get_current_user)):
        check(cookie_id, user)
        name, content = data.name.strip(), data.prompt.strip()
        item_ids = list(dict.fromkeys(data.item_ids))
        if not name or not content or any(not item or len(item) > 100 for item in item_ids):
            raise HTTPException(400, '请填写知识库名称和内容，并选择有效商品')
        with db.lock:
            conn = db.conn
            try:
                conn.execute('BEGIN IMMEDIATE')
                if group_id and not conn.execute('SELECT 1 FROM ai_knowledge_bases WHERE id=? AND cookie_id=?', (group_id, cookie_id)).fetchone():
                    raise HTTPException(404, '知识库不存在')
                for item_id in item_ids:
                    if not conn.execute('SELECT 1 FROM item_info WHERE cookie_id=? AND item_id=?', (cookie_id,item_id)).fetchone():
                        raise HTTPException(400, '所选商品不存在，请刷新后重试')
                if group_id:
                    conn.execute('UPDATE ai_knowledge_bases SET name=?,content=? WHERE id=? AND cookie_id=?', (name,content,group_id,cookie_id))
                    conn.execute('DELETE FROM ai_knowledge_items WHERE knowledge_id=? AND cookie_id=?', (group_id,cookie_id))
                else:
                    group_id = conn.execute('SELECT COALESCE(MAX(id),0)+1 FROM ai_knowledge_bases WHERE cookie_id=?', (cookie_id,)).fetchone()[0]
                    conn.execute('INSERT INTO ai_knowledge_bases(cookie_id,id,name,content) VALUES(?,?,?,?)', (cookie_id,group_id,name,content))
                for item_id in item_ids:
                    conn.execute('''INSERT INTO ai_knowledge_items(cookie_id,item_id,knowledge_id) VALUES(?,?,?)
                        ON CONFLICT(cookie_id,item_id) DO UPDATE SET knowledge_id=excluded.knowledge_id''', (cookie_id,item_id,group_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {'id': group_id}

    @router.delete('/ai-knowledge/{cookie_id}/{group_id}')
    def delete_knowledge(cookie_id: str, group_id: int, user: Dict[str, Any] = Depends(get_current_user)):
        check(cookie_id, user)
        with db.lock:
            db.conn.execute('DELETE FROM ai_knowledge_items WHERE cookie_id=? AND knowledge_id=?', (cookie_id,group_id))
            result = db.conn.execute('DELETE FROM ai_knowledge_bases WHERE cookie_id=? AND id=?', (cookie_id,group_id))
            db.conn.commit()
        if not result.rowcount:
            raise HTTPException(404, '知识库不存在')
        return {'success': True}
    return router
