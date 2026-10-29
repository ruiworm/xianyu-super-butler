import React, { useEffect, useState } from 'react';
import { getItems, getItemAIPrompt, saveItemAIPrompt } from '../services/api';
import { Item } from '../types';
import { notify } from '../services/feedback';

export default function AIProductPrompts({ accountId }: { accountId: string }) {
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingPrompt, setLoadingPrompt] = useState(false);
  const [promptError, setPromptError] = useState(false);
  useEffect(() => {
    let active = true;
    setItems([]); setSelected([]); setPrompt('');
    getItems().then(data => { if (active) setItems(data.filter(item => item.cookie_id === accountId)); })
      .catch(() => { if (active) notify('商品列表加载失败', 'error'); });
    return () => { active = false; };
  }, [accountId]);
  useEffect(() => {
    setPromptError(false);
    if (selected.length !== 1) { setLoadingPrompt(false); return; }
    let active = true;
    setLoadingPrompt(true);
    getItemAIPrompt(accountId, selected[0]).then(result => { if (active) setPrompt(result.prompt); })
      .catch(() => { if (active) { setPromptError(true); notify('提示词加载失败，请重新选择商品', 'error'); } })
      .finally(() => { if (active) setLoadingPrompt(false); });
    return () => { active = false; };
  }, [accountId, selected]);
  const save = async () => {
    if (!selected.length) { notify('请至少选择一个商品', 'warning'); return; }
    setBusy(true);
    let failed = 0;
    for (const id of selected) {
      try { await saveItemAIPrompt(accountId, id, prompt); } catch { failed += 1; }
    }
    setBusy(false);
    notify(failed ? `保存完成，${failed} 个商品保存失败，请重试` : `已保存 ${selected.length} 个商品的提示词`, failed ? 'error' : 'success');
  };
  return <section className="section-panel p-5 space-y-3">
    <h3 className="font-bold">商品 AI 提示词</h3>
    <p className="text-sm text-gray-600">选择一个或多个商品并保存，将替换所选商品已有的专属提示词。留空可清除。</p>
    <div className="max-h-56 overflow-auto space-y-1">{items.map(item => <label key={item.item_id} className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={selected.includes(item.item_id)} onChange={event => setSelected(current => event.target.checked ? [...current, item.item_id] : current.filter(id => id !== item.item_id))} />
      <span>{item.item_title || item.item_id}（{item.item_id}）</span></label>)}</div>
    <textarea aria-label="商品专属 AI 提示词" className="ios-input min-h-28 w-full rounded-md p-3" maxLength={10000} disabled={loadingPrompt} value={prompt} onChange={event => setPrompt(event.target.value)} />
    <button type="button" disabled={busy || loadingPrompt || promptError} onClick={() => void save()} className="ios-btn-primary rounded-md px-4 py-2 text-sm">{busy ? '保存中…' : '保存到所选商品'}</button>
  </section>;
}
