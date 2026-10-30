import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { get, put, del } from '../lib/request';
import { Item } from '../types';
import { confirmAction, notify } from '../services/feedback';

type Rule = { configured?: boolean; mode: 'fixed' | 'percent'; fixed_unit: string; coin_rate: string; worry_rate: string; fee_order: '' | 'parallel' | 'sequential' };
const initial: Rule = { mode: 'percent', fixed_unit: '', coin_rate: '0', worry_rate: '0', fee_order: '' };

export default function ItemSettlementEditor({item, onClose}: {item: Item; onClose: () => void}) {
  const [rule, setRule] = useState<Rule>(initial);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const url = `/item-settlement/${encodeURIComponent(item.cookie_id)}/${encodeURIComponent(item.item_id)}`;
  useEffect(() => {
    let active = true;
    get<Rule>(url).then(data => { if (active && data.configured) setRule({...initial, ...data, fixed_unit: data.fixed_unit ?? ''}); })
      .catch(() => { if (active) setError('读取到手设置失败，请关闭后重试'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [url]);
  const save = async () => {
    const fixed = Number(rule.fixed_unit);
    const coin = Number(rule.coin_rate);
    const worry = Number(rule.worry_rate);
    if (rule.mode === 'fixed' && (!rule.fixed_unit.trim() || !Number.isFinite(fixed) || fixed < 0 || fixed > 10000000)) { notify('请填写有效的每件到手金额', 'warning'); return; }
    if (rule.mode === 'percent' && (!rule.coin_rate.trim() || !Number.isFinite(coin) || coin < 0 || coin > 100 || !rule.worry_rate.trim() || !Number.isFinite(worry) || worry < 0 || worry > 100)) { notify('请选择分润比例，并填写 0 到 100 的无忧卖比例', 'warning'); return; }
    if (rule.mode === 'percent' && worry > 0 && !rule.fee_order) { notify('请选择无忧卖分润的计算方式', 'warning'); return; }
    if (rule.mode === 'percent' && (rule.fee_order === 'sequential' ? (100 - coin) * (100 - worry) / 100 : 100 - coin - worry) < 1.6) { notify('分润和服务费合计不能超过订单金额', 'warning'); return; }
    setBusy(true);
    try {
      await put(url, {mode: rule.mode, fixed_unit: rule.mode === 'fixed' ? rule.fixed_unit : null,
        coin_rate: rule.mode === 'percent' ? rule.coin_rate : '0',
        worry_rate: rule.mode === 'percent' ? rule.worry_rate : '0',
        fee_order: rule.mode === 'percent' ? rule.fee_order : ''});
      window.dispatchEvent(new Event('settlement-rules-changed'));
      notify('商品到手设置已保存', 'success'); onClose();
    } catch { notify('保存失败，请检查输入后重试', 'error'); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!await confirmAction('清除该商品的到手设置？此前订单将显示为未配置估算。')) return;
    setBusy(true);
    try { await del(url); window.dispatchEvent(new Event('settlement-rules-changed')); notify('到手设置已清除', 'success'); onClose(); }
    catch { notify('清除失败，请重试', 'error'); }
    finally { setBusy(false); }
  };
  return createPortal(<div className="modal-overlay"><div className="modal-container">
    <div className="modal-header"><h3 className="font-bold">商品到手设置</h3><p className="text-sm text-gray-500">{item.item_title || item.item_id}</p></div>
    <div className="modal-body space-y-4">
      <p className="text-sm text-gray-600">按当前配置估算已有订单，仅供参考。平台实际到账以订单结算为准。</p>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <label className="block text-sm font-semibold">计算方式<select disabled={loading || !!error} className="ios-input mt-1 w-full rounded p-2" value={rule.mode} onChange={e => setRule({...rule,mode:e.target.value as Rule['mode']})}>
        <option value="percent">按订单金额和分润比例估算</option><option value="fixed">填写每件到手金额</option>
      </select></label>
      {rule.mode === 'fixed' ? <label className="block text-sm font-semibold">每件到手金额（元）<input type="number" min="0" step="0.01" value={rule.fixed_unit} onChange={e => setRule({...rule,fixed_unit:e.target.value})} className="ios-input mt-1 w-full rounded p-2" placeholder="例如 80" /></label> : <>
        <label className="block text-sm font-semibold">闲鱼币分润比例<select value={rule.coin_rate} onChange={e => setRule({...rule,coin_rate:e.target.value})} className="ios-input mt-1 w-full rounded p-2"><option value="">请选择</option><option value="0">不选择闲鱼币分润（0%）</option><option value="5">5%</option><option value="20">20%</option><option value="30">30%</option></select></label>
        <p className="text-xs text-gray-500">按你选择的比例固定计算，买家未使用闲鱼币时也会计算。</p><p className="text-xs text-gray-500">另按订单成交金额固定扣除 1.6% 服务费；填写每件到手金额时不再重复扣费。</p>
        <label className="block text-sm font-semibold">无忧卖分润比例（%）<input type="number" min="0" max="100" step="0.01" value={rule.worry_rate} onChange={e => setRule({...rule,worry_rate:e.target.value})} className="ios-input mt-1 w-full rounded p-2" /></label>
        <p className="text-xs text-gray-500">由你按实际合同填写；不使用无忧卖请填 0。</p>
        {Number(rule.worry_rate) > 0 && <label className="block text-sm font-semibold">无忧卖费用怎么计算<select value={rule.fee_order} onChange={e => setRule({...rule,fee_order:e.target.value as Rule['fee_order']})} className="ios-input mt-1 w-full rounded p-2"><option value="">请选择</option><option value="parallel">两项都按订单金额分别扣除</option><option value="sequential">先扣闲鱼币分润，再按剩余金额扣无忧卖</option></select></label>}
      </>}
    </div>
    <div className="modal-footer flex flex-wrap justify-end gap-2">{rule.configured && <button type="button" disabled={busy} onClick={() => void remove()} className="mr-auto rounded px-3 py-2 text-sm text-red-600">清除设置</button>}<button type="button" disabled={busy} onClick={onClose} className="ios-btn-secondary rounded px-4 py-2">取消</button><button type="button" disabled={loading || busy || !!error} onClick={() => void save()} className="ios-btn-primary rounded px-4 py-2">{busy ? '保存中…' : '保存设置'}</button></div>
  </div></div>, document.body);
}
