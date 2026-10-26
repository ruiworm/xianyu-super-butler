import React, { useEffect, useRef, useState } from 'react';
import {
  createDesktopBridgeToken,
  DesktopBridgeStatus,
  getDesktopBridgeStatus,
  revokeDesktopBridgeToken,
  setDesktopQueries,
} from '../services/api';

const describeError = (error: unknown, fallback: string) => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};

const formatTime = (value: string | number | null | undefined) => {
  if (value == null) return '暂无';
  const time = typeof value === 'number' ? value * 1000 : value;
  const date = new Date(time);
  return Number.isNaN(date.getTime()) ? '暂无' : date.toLocaleString();
};

export default function DesktopBridgeSettings({ accountId, onBusyChange, onSynced }: {
  accountId: string;
  onBusyChange: (busy: boolean) => void;
  onSynced: () => void;
}) {
  const [status, setStatus] = useState<DesktopBridgeStatus | null>(null);
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(false);
  const mutationPending = useRef(false);
  const generation = useRef(0);
  const previousSync = useRef<DesktopBridgeStatus['last_sync_at'] | undefined>(undefined);
  const onSyncedRef = useRef(onSynced);
  onSyncedRef.current = onSynced;

  const applyStatus = (next: DesktopBridgeStatus) => {
    if (previousSync.current !== undefined && next.last_sync_at != null && next.last_sync_at !== previousSync.current) {
      onSyncedRef.current();
    }
    previousSync.current = next.last_sync_at;
    setStatus(next);
    if (!next.enabled) setToken('');
  };

  useEffect(() => {
    let disposed = false;
    let pollPending = false;
    alive.current = true;
    setToken('');
    setStatus(null);
    setLoading(true);
    setError('');
    setNotice('');
    previousSync.current = undefined;
    const refresh = async () => {
      if (mutationPending.current || pollPending) return;
      pollPending = true;
      const requestGeneration = generation.current;
      try {
        const next = await getDesktopBridgeStatus(accountId);
        if (!disposed && requestGeneration === generation.current) {
          applyStatus(next);
          setError('');
        }
      } catch (cause) {
        if (!disposed && requestGeneration === generation.current) {
          setError(describeError(cause, '读取连接状态失败，将自动重试'));
        }
      } finally {
        pollPending = false;
        if (!disposed && requestGeneration === generation.current) setLoading(false);
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 10000);
    return () => {
      disposed = true;
      alive.current = false;
      generation.current += 1;
      window.clearInterval(interval);
    };
  }, [accountId]);

  const changeToken = async (revoke: boolean) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    const operationGeneration = ++generation.current;
    const isCurrent = () => alive.current && operationGeneration === generation.current;
    setBusy(true);
    onBusyChange(true);
    setError('');
    setNotice('');
    try {
      if (revoke) {
        await revokeDesktopBridgeToken(accountId);
        if (!isCurrent()) return;
        setToken('');
        setStatus(current => current ? { ...current, enabled: false, query_enabled: false, query_ready: false } : null);
        setNotice('已断开此账号的电脑连接，旧令牌立即失效。');
      } else {
        const result = await createDesktopBridgeToken(accountId);
        if (!isCurrent()) return;
        setToken(result.token);
        setStatus({ enabled: true, created_at: null, expires_at: result.expires_at, last_seen_at: null, last_sync_at: null });
        previousSync.current = null;
        setNotice('令牌只显示这一次，请复制到电脑连接工具。旧令牌已失效。');
      }
      try {
        const next = await getDesktopBridgeStatus(accountId);
        if (isCurrent()) applyStatus(next);
      } catch {
        if (isCurrent()) setError('操作已完成，但连接状态刷新失败，将自动重试。');
      }
    } catch (cause) {
      if (isCurrent()) setError(describeError(cause, revoke ? '断开连接失败，请重试' : '生成连接令牌失败，请重试'));
    } finally {
      mutationPending.current = false;
      if (isCurrent()) {
        setLoading(false);
        setBusy(false);
        onBusyChange(false);
      }
    }
  };

  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      if (alive.current) setNotice(`${label}已复制`);
    } catch {
      if (alive.current) setNotice('浏览器无法自动复制，请点击输入框选中文本后复制。');
    }
  };

  const changeQueryMode = async (enabled: boolean) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    const operationGeneration = ++generation.current;
    setBusy(true);
    onBusyChange(true);
    setError('');
    const previous = status;
    setStatus(current => current ? { ...current, query_enabled: enabled } : current);
    try {
      const next = await setDesktopQueries(accountId, enabled);
      if (alive.current && generation.current === operationGeneration) {
        applyStatus(next);
        setNotice(enabled ? '商品和订单查询已切换到电脑客户端。' : '商品和订单查询已恢复为服务器直连。');
      }
    } catch (cause) {
      if (alive.current && generation.current === operationGeneration) {
        setStatus(previous);
        setError(describeError(cause, '切换查询方式失败'));
      }
    } finally {
      mutationPending.current = false;
      if (alive.current && generation.current === operationGeneration) {
        setBusy(false);
        onBusyChange(false);
      }
    }
  };

  const localServer = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

  return <div className="space-y-4">
    <p className="text-sm text-gray-600">电脑运行连接工具并选择闲鱼卖家客服；登录、手动完成滑块后，Cookie 自动同步到服务器。连接工具需要保持运行。</p>
    <p className="text-xs text-gray-500">需客户端支持本机调试连接；连接工具会检测是否可用。平台仍可能要求再次验证，同步成功表示服务器收到了会话。</p>
    <ol className="list-inside list-decimal space-y-2 text-sm text-gray-700">
      <li>在 Windows 电脑下载并解压连接工具，双击 start.bat。</li>
      <li>复制下方服务器地址和连接令牌到工具中，选择已安装的闲鱼卖家客服。</li>
      <li>启动连接，在客户端中登录并手动完成验证，查看下方同步时间。</li>
    </ol>
    <a href="/desktop-bridge/download" download
      className="inline-flex rounded-md bg-gray-100 px-4 py-2 text-sm font-bold text-gray-700 hover:bg-gray-200">下载 Windows 连接工具</a>
    <label className="block text-sm font-bold" htmlFor="desktop-bridge-server">服务器地址</label>
    <div className="flex items-center gap-2">
      <input id="desktop-bridge-server" className="ios-input min-w-0 flex-1 rounded-md p-2 text-sm font-normal" readOnly
        value={window.location.origin} onClick={event => event.currentTarget.select()} />
      <button type="button" className="ios-btn-secondary shrink-0 rounded-md px-3 py-2 text-sm" onClick={() => void copy(window.location.origin, '服务器地址')}>复制</button>
    </div>
    {window.location.protocol !== 'https:' && !localServer && <p className="text-xs text-amber-700" role="note">当前地址使用 HTTP。远程同步请先配置 HTTPS，也可通过本机 SSH 隧道连接；连接工具仅允许 HTTPS 或本机 HTTP 地址。</p>}
    <div className="rounded-md bg-gray-50 p-3 text-sm">
      <p className="mb-2 font-bold">{loading ? '读取连接状态…' : status?.enabled ? '已创建连接凭据' : '未启用电脑自动同步'}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-gray-600">
        <dt>最近连接</dt><dd>{formatTime(status?.last_seen_at)}</dd>
        <dt>最近同步</dt><dd>{formatTime(status?.last_sync_at)}</dd>
        <dt>令牌到期</dt><dd>{formatTime(status?.expires_at)}</dd>
        <dt>客户端查询接口</dt><dd>{status?.query_ready ? '已就绪' : '未连接或当前页面不支持'}</dd>
      </dl>
    </div>
    <div className="rounded-md border border-gray-200 p-3 text-sm">
      <label className="flex items-center gap-2 font-bold">
        <input type="checkbox" checked={status?.query_enabled === true} disabled={busy || loading || !status?.enabled}
          onChange={event => void changeQueryMode(event.target.checked)} />
        商品和订单通过电脑客户端查询
      </label>
      <p className="mt-2 text-xs text-gray-600">支持商品列表、商品详情、卖出订单列表、订单数量及订单详情。请保持电脑和连接工具在线，并在客户端打开商品或订单页面。</p>
      <p className="mt-1 text-xs text-gray-500">开启后，客户端不可用时查询会提示失败。消息收发和发货仍由服务器处理。</p>
    </div>
    {token && <div className="space-y-2">
      <label htmlFor="desktop-bridge-token" className="block text-sm font-bold">连接令牌（关闭窗口后不再显示）</label>
      <div className="flex items-center gap-2">
        <input id="desktop-bridge-token" readOnly value={token} autoComplete="off" spellCheck={false}
          onClick={event => event.currentTarget.select()} className="ios-input min-w-0 flex-1 rounded-md p-2 font-mono text-xs" />
        <button type="button" onClick={() => void copy(token, '连接令牌')} className="ios-btn-secondary shrink-0 rounded-md px-3 py-2 text-sm">复制</button>
      </div>
      <p className="text-xs text-gray-500">令牌用于此账号的会话同步和已开启的商品、订单查询，请只交给自己的电脑连接工具。</p>
    </div>}
    <div className="flex flex-wrap gap-2">
      <button type="button" disabled={busy || loading} onClick={() => void changeToken(false)} className="ios-btn-primary rounded-md px-4 py-2 text-sm disabled:opacity-50">
        {busy ? '处理中…' : status?.enabled ? '重新生成令牌' : '生成连接令牌'}
      </button>
      {status?.enabled && <button type="button" disabled={busy} onClick={() => void changeToken(true)}
        className="rounded-md bg-red-50 px-4 py-2 text-sm text-red-700 disabled:opacity-50">断开电脑连接</button>}
    </div>
    {status?.enabled && <p className="text-xs text-gray-500">重新生成后，需要在电脑工具中替换令牌。断开连接会立即停止后续同步。</p>}
    {notice && <p role="status" className="text-sm text-gray-600">{notice}</p>}
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
  </div>;
}
