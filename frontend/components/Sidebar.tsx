import React, { useState } from 'react';
import {
  BellRing,
  Bot,
  Box,
  CreditCard,
  LayoutDashboard,
  ListFilter,
  LogOut,
  Settings,
  ShoppingBag,
  Star,
  UserCog,
  Users,
  Workflow,
  X,
} from 'lucide-react';

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  onLogout: () => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
  isAdmin?: boolean;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, onLogout, mobileOpen, onMobileClose, isAdmin = false, collapsed, onToggleCollapse }) => {
  const [expanded, setExpanded] = useState(false);
  const [editingFavorites, setEditingFavorites] = useState(false);
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const value = JSON.parse(localStorage.getItem('sidebar_favorites') || '[]');
      return Array.isArray(value) ? [...new Set<string>(value.filter((id): id is string => typeof id === 'string').map(id => id === 'auto-reply' ? 'ai-reply' : id))] : [];
    } catch { return []; }
  });
  const toggleFavorite = (id: string) => setFavorites(current => {
    const next = current.includes(id) ? current.filter(value => value !== id) : [...current, id];
    try { localStorage.setItem('sidebar_favorites', JSON.stringify(next)); } catch { /* storage unavailable */ }
    return next;
  });
  // 系统级页面只对管理员开放：普通用户看不到入口，后端接口也会拒绝其访问。
  // 「通知与日志」保留：通知渠道按用户隔离（页面内系统日志已单独按权限隐藏）。
  const adminOnly = (item: { id: string }) => isAdmin || !['settings', 'users'].includes(item.id);

  const menuGroups = [
    {
      label: '经营概览',
      items: [
        { id: 'dashboard', icon: LayoutDashboard, label: '总览' },
      ],
    },
    {
      label: '闲鱼业务',
      items: [
        { id: 'accounts', icon: Users, label: '账号管理' },
        { id: 'items', icon: Box, label: '商品与发货' },
        { id: 'orders', icon: ShoppingBag, label: '订单管理' },
        { id: 'buyer-interaction', icon: Star, label: '买家互动' },
        { id: 'cards', icon: CreditCard, label: '卡密库存' },
      ],
    },
    {
      label: '客户沟通',
      items: [
        { id: 'messages', icon: ListFilter, label: '消息中心' },
        { id: 'ai-reply', icon: Bot, label: 'AI 回复' },
      ],
    },
    {
      label: '自动化',
      items: [
        { id: 'product-automation', icon: Workflow, label: '商品自动化' },
      ],
    },
    {
      label: '系统',
      items: [
        { id: 'users', icon: UserCog, label: '用户管理' },
        { id: 'notifications', icon: BellRing, label: '通知与日志' },
        { id: 'settings', icon: Settings, label: '系统设置' },
      ].filter(adminOnly),
    },
  ];

  return (
    <>
    {mobileOpen && (
      <button
        type="button"
        className="fixed inset-0 z-30 bg-black/40 lg:hidden"
        onClick={onMobileClose}
        aria-label="关闭导航"
      />
    )}
    <aside className={`fixed left-0 top-0 z-40 flex h-screen w-[248px] flex-col bg-[var(--surface)] transition-all lg:translate-x-0 ${collapsed ? 'lg:w-[64px]' : ''} ${
      mobileOpen ? 'translate-x-0' : '-translate-x-full'
    }`} style={{ borderRight: '1px solid var(--border)' }}>
      <div className="flex min-h-0 flex-1 flex-col">
        {/* 品牌区：黄色渐变徽标 + 圆角，作为整站视觉锚点 */}
        <div className="flex h-[76px] shrink-0 items-center gap-3 px-5">
          <div
            className={`flex h-10 w-10 items-center justify-center rounded-xl ${collapsed ? "lg:hidden" : ""}`}
            style={{
              background: 'linear-gradient(140deg, var(--brand-300), var(--brand))',
              boxShadow: 'var(--shadow-brand)',
            }}
          >
            <span className="text-lg font-black text-[#2a2416]">闲</span>
          </div>
          <div className={`min-w-0 flex-1 ${collapsed ? "lg:hidden" : ""}`}>
            <p className="truncate text-base font-extrabold text-[var(--text)]">闲鱼超级管家</p>
            <p className="mt-0.5 text-[11px] font-medium text-[var(--text-soft)]">运营工作台</p>
          </div>
          <button type="button" onClick={onToggleCollapse} className="hidden rounded-full p-2 hover:bg-gray-100 lg:block" aria-label={collapsed ? "展开侧栏" : "收起侧栏"}>{collapsed ? "›" : "‹"}</button>
          <button type="button" onClick={onMobileClose} className="rounded-full p-2 hover:bg-[var(--surface-hover)] lg:hidden" aria-label="关闭导航">
            <X className="w-5 h-5" />
          </button>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
          {collapsed && <div className="hidden space-y-1 lg:block">{menuGroups.flatMap(group => group.items).filter(item => favorites.includes(item.id) || item.id === activeTab).map(item => { const Icon = item.icon; return <button key={item.id} type="button" title={item.label} aria-label={item.label} onClick={() => setActiveTab(item.id)} className={`flex h-10 w-10 items-center justify-center rounded-lg ${activeTab === item.id ? 'bg-yellow-300' : 'hover:bg-gray-100'}`}><Icon className="h-5 w-5" /></button>; })}</div>}
          <div className={collapsed ? 'lg:hidden' : ''}>
          <div className="mb-2 flex items-center justify-between px-3"><p className="text-xs font-bold text-gray-500">收藏功能</p><button type="button" onClick={() => { setEditingFavorites(!editingFavorites); if (!editingFavorites) setExpanded(true); }} className="text-xs text-gray-600">{editingFavorites ? "完成" : "编辑"}</button></div>
          {menuGroups.flatMap(group => group.items).filter(item => favorites.includes(item.id)).map(item => {
            const Icon = item.icon;
            return <div key={item.id} className="flex items-center"><button type="button" onClick={() => setActiveTab(item.id)} className={`flex min-h-11 flex-1 items-center gap-3 rounded-xl px-3 text-sm ${activeTab === item.id ? 'bg-yellow-300 font-bold' : 'hover:bg-gray-100'}`}><Icon className="h-[18px] w-[18px]" />{item.label}</button>{editingFavorites && <button type="button" onClick={() => toggleFavorite(item.id)} aria-label={`取消收藏${item.label}`} className="p-2 text-yellow-600">★</button>}</div>;
          })}
          {!favorites.length && <p className="px-3 py-2 text-xs text-gray-500">展开全部功能，点击星标收藏常用功能。</p>}
          <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded} className="mt-3 w-full rounded-lg bg-gray-100 px-3 py-2 text-left text-sm font-semibold">{expanded ? '收起全部功能' : '展开全部功能'}</button>
          {expanded && menuGroups.map(group => <div key={group.label} className="mt-4"><p className="mb-1 px-3 text-xs font-bold text-gray-500">{group.label}</p>{group.items.map(item => { const Icon = item.icon; return <div key={item.id} className="flex items-center"><button type="button" onClick={() => setActiveTab(item.id)} className={`flex min-h-11 flex-1 items-center gap-3 rounded-xl px-3 text-sm ${activeTab === item.id ? 'bg-yellow-300 font-bold' : 'hover:bg-gray-100'}`}><Icon className="h-[18px] w-[18px]" />{item.label}</button>{editingFavorites && <button type="button" onClick={() => toggleFavorite(item.id)} aria-label={`${favorites.includes(item.id) ? '取消收藏' : '收藏'}${item.label}`} className="p-2 text-yellow-600">{favorites.includes(item.id) ? '★' : '☆'}</button>}</div>; })}</div>)}
          </div>
        </nav>
      </div>

      <div className="shrink-0 p-3" style={{ borderTop: '1px solid var(--border)' }}>
        <button
          type="button"
          onClick={onLogout}
          className="flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-[var(--text-muted)] transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
        >
          <LogOut className="h-[18px] w-[18px]" />
          <span className={`text-sm font-medium ${collapsed ? "lg:hidden" : ""}`}>退出登录</span>
        </button>
      </div>
    </aside>
    </>
  );
};

export default Sidebar;
