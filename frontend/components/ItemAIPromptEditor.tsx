import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Item } from '../types';
import { getItemAIPrompt, saveItemAIPrompt } from '../services/api';
import { notify } from '../services/feedback';

export default function ItemAIPromptEditor({ item, onClose }: { item: Item; onClose: () => void }) {
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    getItemAIPrompt(item.cookie_id, item.item_id)
      .then(data => { if (active) setPrompt(data.prompt); })
      .catch(() => { if (active) setError('提示词加载失败，请关闭后重试'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [item.cookie_id, item.item_id]);
  const save = async () => {
    setSaving(true);
    try {
      await saveItemAIPrompt(item.cookie_id, item.item_id, prompt);
      notify('商品 AI 提示词已保存', 'success');
      onClose();
    } catch { notify('保存失败，请重试', 'error'); }
    finally { setSaving(false); }
  };
  return createPortal(<div className="modal-overlay"><div className="modal-container">
    <div className="modal-header"><h3 className="text-lg font-bold">商品 AI 提示词</h3>
      <p className="text-sm text-gray-500">{item.item_title || item.item_id}</p></div>
    <div className="modal-body space-y-3">
      <p className="text-sm text-gray-600">仅影响当前账号的此商品。沿用账号的模型、密钥和通用规则，追加商品专属说明；留空则只使用账号设置。请先在 AI 回复页面启用账号 AI。</p>
      <label className="block text-sm font-bold">商品专属规则
        <textarea className="ios-input mt-2 min-h-64 w-full rounded-md p-3" value={prompt}
          maxLength={10000} disabled={loading || saving || Boolean(error)} onChange={e => setPrompt(e.target.value)}
          placeholder="例如：这是软件激活密钥，交付包含兑换链接。只介绍本商品的使用方法，不承诺额外授权。" />
      </label>
      {loading && <p>正在加载…</p>}{error && <p role="alert" className="text-red-600">{error}</p>}
    </div>
    <div className="modal-footer flex justify-end gap-2">
      <button className="ios-btn-secondary rounded-md px-4 py-2" onClick={onClose} disabled={saving}>取消</button>
      <button className="ios-btn-primary rounded-md px-4 py-2" onClick={() => void save()} disabled={loading || saving || Boolean(error)}>{saving ? '保存中…' : '保存提示词'}</button>
    </div>
  </div></div>, document.body);
}
