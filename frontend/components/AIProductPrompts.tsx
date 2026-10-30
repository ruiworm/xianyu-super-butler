import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { getItems } from '../services/api';
import { get, put, del } from '../lib/request';
import { Item } from '../types';
import { confirmAction, notify } from '../services/feedback';

type PromptGroup = { id: number; name: string; prompt: string; item_ids: string[] };
const endpoint = (id: string, knowledge: boolean) => `/${knowledge ? 'ai-knowledge' : 'ai-prompt-groups'}/${encodeURIComponent(id)}`;

export default function AIProductPrompts({ accountId, knowledge = false }: { accountId: string; knowledge?: boolean }) {
  const label = knowledge ? '知识库' : '提示词';
  const [items, setItems] = useState<Item[]>([]);
  const [groups, setGroups] = useState<PromptGroup[]>([]);
  const [editing, setEditing] = useState<PromptGroup | null>(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const reload = async () => {
    const [products, result] = await Promise.all([getItems(), get<{groups: PromptGroup[]}>(endpoint(accountId, knowledge))]);
    setItems(products.filter(item => item.cookie_id === accountId));
    setGroups(result.groups);
  };
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setEditing(null);
    Promise.all([getItems(), get<{groups: PromptGroup[]}>(endpoint(accountId, knowledge))])
      .then(([products, result]) => { if (active) { setItems(products.filter(item => item.cookie_id === accountId)); setGroups(result.groups); } })
      .catch(() => { if (active) setError(`${label}或商品加载失败，请刷新后重试`); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [accountId, knowledge]);
  const filtered = useMemo(() => items.filter(item => `${item.item_title || ''} ${item.item_id}`.toLowerCase().includes(query.trim().toLowerCase())), [items, query]);
  const save = async () => {
    if (!editing || !editing.name.trim() || !editing.prompt.trim()) { notify(`请填写${label}名称和内容`, 'warning'); return; }
    setBusy(true);
    try {
      const result = await put<{id: number}>(`${endpoint(accountId, knowledge)}/${editing.id}`, { name: editing.name, prompt: editing.prompt, item_ids: editing.item_ids });
      // The write already succeeded. Keep its id even if refreshing the list
      // fails, so a retry updates the same rule instead of creating a duplicate.
      setEditing(current => current ? { ...current, id: result.id } : current);
      try {
        await reload(); setEditing(null); notify(`${label}已保存`, 'success');
      } catch {
        notify(`${label}已保存，但列表刷新失败，请稍后重试`, 'warning');
      }
    } catch { notify('保存失败，请检查商品后重试', 'error'); }
    finally { setBusy(false); }
  };
  const remove = async (group: PromptGroup) => {
    if (!await confirmAction(`删除“${group.name}”？关联商品将不再使用这个${label}。`)) return;
    setBusy(true);
    try {
      await del(`${endpoint(accountId, knowledge)}/${group.id}`);
      setGroups(current => current.filter(item => item.id !== group.id));
      try { await reload(); notify(`${label}已删除`, 'success'); }
      catch { notify(`${label}已删除，但列表刷新失败，请稍后重试`, 'warning'); }
    }
    catch { notify('删除失败，请重试', 'error'); }
    finally { setBusy(false); }
  };
  return <section className="section-panel p-5 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-bold">{knowledge ? "商品知识库" : "商品 AI 提示词"}</h3><p className="mt-1 text-sm text-gray-600">{knowledge ? "填写商品教程和常见问答，一份资料可关联多个商品；每个商品使用一个知识库。" : "一条提示词可关联多个商品，每个商品使用一条商品专属提示词。"}</p></div>
      <button type="button" disabled={loading || busy || Boolean(error)} onClick={() => { setQuery(''); setEditing({id: 0, name: '', prompt: '', item_ids: []}); }} className="ios-btn-primary rounded-md px-4 py-2 text-sm">添加{label}</button></div>
    {loading && <p className="text-sm text-gray-500">加载中…</p>}{error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {!loading && !error && !groups.length && <p className="text-sm text-gray-500">还没有{label}，点击“添加{label}”开始设置。</p>}
    {groups.map(group => <div key={group.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-gray-200 p-3"><div className="min-w-0"><p className="font-semibold">{group.name}</p><p className="text-xs text-gray-500">关联 {group.item_ids.length} 个商品</p></div><div className="flex gap-2"><button type="button" className="ios-btn-secondary rounded px-3 py-1 text-sm" onClick={() => { setQuery(''); setEditing({...group, item_ids: [...group.item_ids]}); }}>编辑</button><button type="button" className="rounded px-3 py-1 text-sm text-red-600" disabled={busy} onClick={() => void remove(group)}>删除</button></div></div>)}
    {editing && createPortal(<div className="modal-overlay"><div className="modal-container"><div className="modal-header"><h3 className="font-bold">{`${editing.id ? '编辑' : '添加'}${label}`}</h3></div><div className="modal-body space-y-3">
      <label className="block text-sm font-semibold">{label}名称<input className="ios-input mt-1 w-full rounded p-2" maxLength={80} value={editing.name} onChange={event => setEditing({...editing, name: event.target.value})} placeholder="例如：软件售后" /></label>
      <label className="block text-sm font-semibold">{label}内容<textarea className="ios-input mt-1 min-h-32 w-full rounded p-2" maxLength={10000} value={editing.prompt} onChange={event => setEditing({...editing, prompt: event.target.value})} placeholder={knowledge ? "填写使用教程、常见问题与售后说明" : "填写这些商品共用的 AI 回复规则"} /></label>
      <details className="rounded-md border border-gray-200 p-3">
        <summary className="cursor-pointer text-sm font-semibold">选择商品（已选 {editing.item_ids.length} 个）</summary>
        <p className="mt-2 text-xs text-gray-500">选择已绑定其他{label}的商品时，会将它改为使用当前{label}。</p>
        <input aria-label="搜索商品" className="ios-input my-2 w-full rounded p-2" placeholder="搜索商品名称或 ID" value={query} onChange={event => setQuery(event.target.value)} />
        <div className="max-h-48 space-y-1 overflow-auto">{filtered.map(item => { const other = groups.find(group => group.id !== editing.id && group.item_ids.includes(item.item_id)); return <label key={item.item_id} className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-gray-50"><input type="checkbox" checked={editing.item_ids.includes(item.item_id)} onChange={event => setEditing({...editing, item_ids: event.target.checked ? [...editing.item_ids, item.item_id] : editing.item_ids.filter(id => id !== item.item_id)})} /><span>{item.item_title || item.item_id}（{item.item_id}）{other && <span className="text-gray-400"> · 当前：{other.name}</span>}</span></label>; })}{!filtered.length && <p className="text-sm text-gray-500">没有找到商品</p>}</div>
      </details>
      </div><div className="modal-footer flex justify-end gap-2"><button type="button" disabled={busy} onClick={() => setEditing(null)} className="ios-btn-secondary rounded px-4 py-2">取消</button><button type="button" disabled={busy} onClick={() => void save()} className="ios-btn-primary rounded px-4 py-2">{busy ? '保存中…' : `保存${label}`}</button></div></div></div>, document.body)}
  </section>;
}
