import React, { useEffect, useState } from 'react';
import { get } from '../lib/request';

type Job = { cookie_id: string; order_id: string; item_id: string; due_at: number; state: 'waiting' | 'processing' | 'review'; updated_at: number };

export default function DelayedDeliveryJobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () => get<{jobs: Job[]}>('/delayed-delivery-jobs')
      .then(result => { if (active) { setJobs(result.jobs); setError(false); } })
      .catch(() => { if (active) setError(true); });
    void refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  if (!jobs.length && !error) return null;
  return <section className="section-panel p-4 space-y-2"><h3 className="font-bold">延时发货任务</h3>
    <p className="text-xs text-gray-600">等待中的任务在账号重新连接后继续；处理中断的订单不会自动重发，请先核对买家是否已收到。</p>
    {error && <p role="alert" className="text-sm text-red-600">延时任务状态加载失败，请稍后刷新。</p>}
    {jobs.map(job => <div key={`${job.cookie_id}:${job.order_id}`} className="flex flex-wrap justify-between gap-2 border-t border-gray-100 pt-2 text-sm">
      <span>订单 {job.order_id} · 商品 {job.item_id}</span>
      <span className={job.state === 'review' ? 'text-red-600' : 'text-gray-600'}>{job.state === 'waiting' ? `等待至 ${new Date(job.due_at * 1000).toLocaleString()}` : job.state === 'review' ? '需人工核对，未自动重发' : '处理中（若重启后停留，请人工核对）'}</span>
    </div>)}
  </section>;
}
