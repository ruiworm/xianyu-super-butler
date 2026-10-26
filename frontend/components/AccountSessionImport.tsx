import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail } from '../types';
import { importAccountSession } from '../services/api';
import { notify } from '../services/feedback';
import DesktopBridgeSettings from './DesktopBridgeSettings';

export default function AccountSessionImport({ account, onClose, onSaved }: {
  account: AccountDetail; onClose: () => void; onSaved: () => void;
}) {
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [mode, setMode] = useState<'automatic' | 'manual'>('automatic');
  return createPortal(<div className="modal-overlay"><div className="modal-container" role="dialog" aria-modal="true" aria-labelledby="account-session-title">
    <div className="modal-header"><h3 id="account-session-title" className="text-lg font-bold">桌面客户端接入</h3>
      <p className="text-sm text-gray-500">{account.nickname || account.id}</p></div>
    <div className="modal-body space-y-3">
      <div role="tablist" aria-label="会话同步方式" className="flex gap-2 border-b border-gray-100 pb-3">
        {([{ id: 'automatic', label: '自动同步' }, { id: 'manual', label: '手动导入' }] as const).map(tab =>
          <button key={tab.id} id={`session-tab-${tab.id}`} type="button" role="tab" aria-selected={mode === tab.id}
            aria-controls={`session-panel-${tab.id}`} disabled={saving} onClick={() => { setMode(tab.id); setMessage(''); }}
            className={`rounded-md px-4 py-2 text-sm font-bold disabled:opacity-50 ${mode === tab.id ? 'bg-amber-100 text-amber-900' : 'text-gray-500 hover:bg-gray-100'}`}>{tab.label}</button>)}
      </div>
      <div key={account.id} id="session-panel-automatic" role="tabpanel" aria-labelledby="session-tab-automatic" hidden={mode !== 'automatic'}>
        <DesktopBridgeSettings accountId={account.id} onBusyChange={setSaving} onSynced={onSaved} />
      </div>
      <div id="session-panel-manual" role="tabpanel" aria-labelledby="session-tab-manual" hidden={mode !== 'manual'} className="space-y-3">
      <p className="text-sm text-gray-600">在自己的电脑上手动登录并完成平台要求的验证，再把该账号的完整 Cookie 粘贴到这里。服务器会核对账号身份、保存会话并重新连接。</p>
      <p className="text-sm text-gray-600">若暂时无法连接桌面客户端，可在这里导入同一账号的网页会话。</p>
      <label className="block text-sm font-bold">完整 Cookie
        <textarea value={value} maxLength={32000} spellCheck={false} autoComplete="off" disabled={saving}
          onChange={e => setValue(e.target.value)} className="ios-input mt-2 min-h-32 w-full rounded-md p-3 font-mono text-sm"
          placeholder="unb=…; _m_h5_tk=…; 其他 Cookie…" /></label>
      <p className="text-xs text-gray-500">Cookie 是账号凭据，只提交到你自己的管家服务。远程同步请通过 HTTPS 访问；不会写入浏览器本地存储。</p>
      {message && <p role="alert" className="text-sm text-red-600">{message}</p>}
      </div>
    </div>
    <div className="modal-footer flex justify-end gap-2">
      <button disabled={saving} className="ios-btn-secondary rounded-md px-4 py-2" onClick={onClose}>关闭</button>
      {mode === 'manual' && <button disabled={saving || !value.trim()} className="ios-btn-primary rounded-md px-4 py-2" onClick={async () => {
        setSaving(true); setMessage('');
        try {
          const result = await importAccountSession(account.id, value);
          setValue(''); onSaved(); onClose();
          notify(result.message, 'info');
        } catch (error) {
          const detail = (error as any)?.response?.data?.detail;
          setMessage(typeof detail === 'string' ? detail : '同步失败，请重试');
        }
        finally { setSaving(false); }
      }}>{saving ? '同步中…' : '同步到服务器'}</button>}
    </div>
  </div></div>, document.body);
}
