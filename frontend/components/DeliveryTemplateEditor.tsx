import React from 'react';

interface Props {
  enabled: boolean;
  template: string;
  onChange: (enabled: boolean, template: string) => void;
}

export default function DeliveryTemplateEditor({ enabled, template, onChange }: Props) {
  return <div className="space-y-3 rounded-md border border-gray-200 p-4">
    <label className="flex items-center gap-2 font-bold text-gray-900">
      <input type="checkbox" checked={enabled} onChange={e => onChange(e.target.checked, template)} />
      多参数发货模板
    </label>
    <p className="text-xs text-gray-500">一行库存对应一份完整商品，密钥和链接一起发送、一起扣减。每单多份会按完整记录发出。</p>
    {enabled && <>
      <label className="block text-sm font-bold">发送给买家的内容
        <textarea value={template} onChange={e => onChange(true, e.target.value)}
          className="ios-input mt-2 min-h-32 w-full rounded-md p-3"
          placeholder={'密钥：{参数1}\n链接：{参数2}\n使用说明：付款后即可兑换'} />
      </label>
      <p className="text-xs text-gray-600">库存示例：KEY-001----https://example.com/redeem，对应 {'{参数1}'} 和 {'{参数2}'}。只发密钥时可把固定链接直接写在模板中，使用 {'{参数}'} 插入整行库存。</p>
      <p className="text-xs text-gray-600">也支持每行 JSON：{'{"密钥":"KEY-001","链接":"https://example.com"}'}，模板填写 {'密钥：{密钥}，链接：{链接}'}。缺少参数会停止发货，保留库存。</p>
    </>}
  </div>;
}
