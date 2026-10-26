import React, { useEffect, useState } from 'react';
import { Card } from '../types';
import { updateCard } from '../services/api';
import { notify } from '../services/feedback';
import DeliveryTemplateEditor from './DeliveryTemplateEditor';

export default function CardDeliveryTemplateSettings({ card, onSaved }: { card: Card; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(Boolean(card.delivery_template_enabled));
  const [template, setTemplate] = useState(card.delivery_template || '密钥：{参数1}\n链接：{参数2}');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setEnabled(Boolean(card.delivery_template_enabled));
    setTemplate(card.delivery_template || '密钥：{参数1}\n链接：{参数2}');
  }, [card.id, card.delivery_template, card.delivery_template_enabled]);
  if (card.type === 'image') return null;
  return <div className="space-y-2 sm:col-span-2">
    <DeliveryTemplateEditor enabled={enabled} template={template} onChange={(value, text) => { setEnabled(value); setTemplate(text); }} />
    <p className="text-xs text-gray-500">保存到库存「{card.name}」，所有绑定此库存的商品和通用规则都会使用它。</p>
    <button type="button" disabled={saving} className="ios-btn-secondary rounded-md px-3 py-2 text-sm"
      onClick={async () => {
        setSaving(true);
        try {
          await updateCard(card.id, { enabled: card.enabled, delivery_template_enabled: enabled, delivery_template: template });
          onSaved();
          notify('库存发货模板已保存', 'success');
        } catch { notify('模板保存失败，请检查内容后重试', 'error'); }
        finally { setSaving(false); }
      }}>{saving ? '保存中…' : '保存此库存发货模板'}</button>
  </div>;
}
