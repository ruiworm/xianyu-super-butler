import React, { useEffect, useRef, useState } from 'react';
import { AIReplyImageRule, getAIReplyImages, saveAIReplyImages, uploadImage } from '../services/api';

export default function AIReplyImages({ accountId }: { accountId: string }) {
  const [rules, setRules] = useState<AIReplyImageRule[]>([]);
  const [busy, setBusy] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let disposed = false;
    getAIReplyImages(accountId).then(result => {
      if (!disposed) { setRules(result.rules); setLoaded(true); }
    }).catch(() => { if (!disposed) setMessage('读取图片规则失败，请重新选择账号重试'); })
      .finally(() => { if (!disposed) setBusy(false); });
    return () => { disposed = true; alive.current = false; };
  }, [accountId]);
  const update = (id: string, changes: Partial<AIReplyImageRule>) => setRules(items => items.map(rule => rule.id === id ? { ...rule, ...changes } : rule));
  const errorText = (error: any) => typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : '操作失败，请检查图片、名称和发送场景后重试';
  return <section className="section-panel space-y-4 p-5">
    <div><h2 className="font-bold">按意图发送固定图片</h2>
      <p className="mt-1 text-sm text-gray-500">上传图片，说明什么时候发送。例如“安装步骤图”：买家问怎么安装、怎么接线时发送。需启用当前账号 AI；关键词回复优先。</p>
      <p className="mt-1 text-xs text-gray-500">每次最多选一张；没有明确匹配则继续文字回复。图片用途由你填写，AI 不会自动识别图片内容。</p></div>
    <p className="text-xs text-gray-500">使用当前账号配置的模型；启用图片规则后，会产生额外的意图识别用量。</p>
    {rules.map(rule => <fieldset key={rule.id} disabled={busy} className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={rule.enabled} onChange={e => update(rule.id, { enabled: e.target.checked })} />启用</label>
        <button type="button" className="text-sm text-red-600" onClick={() => setRules(items => items.filter(item => item.id !== rule.id))}>移除此规则</button>
      </div>
      <label className="block text-sm">图片名称<input className="ios-input mt-1 w-full rounded-md p-2" maxLength={80} value={rule.title} onChange={e => update(rule.id, { title: e.target.value })} placeholder="例如：安装步骤图" /></label>
      <label className="block text-sm">发送场景<textarea className="ios-input mt-1 w-full rounded-md p-2" maxLength={1000} value={rule.intent} onChange={e => update(rule.id, { intent: e.target.value })} placeholder="例如：买家咨询安装、接线方法，或要求查看安装说明。只问价格时不要发。" /></label>
      <label className="block text-sm">限定商品 ID（留空适用于本账号所有商品）<input className="ios-input mt-1 w-full rounded-md p-2" maxLength={100} value={rule.item_id} onChange={e => update(rule.id, { item_id: e.target.value.trim() })} /></label>
      {rule.image_url && <img className="max-h-48 max-w-full rounded-md object-contain" src={rule.image_url} alt={rule.title || '已上传图片'} />}
      <label className="block text-sm">{rule.image_url ? '替换图片' : '上传固定图片'}（最大 5MB）<input type="file" accept="image/jpeg,image/png,image/gif,image/webp" className="mt-2 block text-sm" onChange={async e => {
        const file = e.target.files?.[0]; e.target.value = '';
        if (!file) return;
        if (file.size > 5 * 1024 * 1024) { setMessage('请选择不超过 5MB 的图片'); return; }
        setBusy(true); setMessage('');
        try { const result = await uploadImage(file); if (alive.current) update(rule.id, { image_url: '/' + result.image_url.replace(/^\/+/, '') }); }
        catch (error) { if (alive.current) setMessage(errorText(error)); }
        finally { if (alive.current) setBusy(false); }
      }} /></label>
    </fieldset>)}
    <div className="flex gap-3">
      <button type="button" disabled={busy || !loaded || rules.length >= 30} className="ios-btn-secondary rounded-md px-4 py-2 text-sm" onClick={() => {
        setRules(items => [...items, { id: `img_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`, title: '', intent: '', image_url: '', item_id: '', enabled: true }]); setMessage('');
      }}>添加图片规则</button>
      <button type="button" disabled={busy || !loaded || rules.some(rule => !rule.title.trim() || !rule.intent.trim() || !rule.image_url)} className="ios-btn-primary rounded-md px-4 py-2 text-sm" onClick={async () => {
        setBusy(true); setMessage('');
        try { const result = await saveAIReplyImages(accountId, rules); if (alive.current) { setRules(result.rules); setMessage('图片规则已保存'); } }
        catch (error) { if (alive.current) setMessage(errorText(error)); }
        finally { if (alive.current) setBusy(false); }
      }}>{busy ? '处理中…' : '保存图片规则'}</button>
    </div>
    <p className="text-xs text-gray-500">添加、修改、停用或移除后，点击“保存图片规则”生效。最多 30 条；同名规则优先使用当前商品专属图片。</p>
    {message && <p role="status" className="text-sm">{message}</p>}
  </section>;
}
