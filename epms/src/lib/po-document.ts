import type { ApiPo } from '@/services/po'
import type { CompanyConfig } from '@/services/config'
import { formatAmount, formatDate } from '@/lib/utils'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Materials / Packaging',
  2: 'Consumables / Misc',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project-Related',
}

const TAX_LABELS: Record<number, string> = {
  0: 'Exempt (0%)',
  0.05: 'GST (5%)',
  0.13: 'HST (13%)',
  0.15: 'HST (15%)',
}

export function generatePoHtml(po: ApiPo, config: CompanyConfig): string {
  const tpl = config.pdf_templates?.po ?? { show_logo: true, header_note: '', footer_note: '', show_terms: true, terms_text: '' }
  const hasMaterial = po.type === 1 || po.type === 3
  const hasSample = po.line_items.some((item) => item.sample)
  // Same split as pdf_po.py: purchase_orders.notes belongs to the NC mirror —
  // every sync rewrites it with NC's own memo plus [NC Paid] / [NC Closed
  // <date>] markers that finance reads internally. Falling back to it on an
  // NC PO would print those internal markers on this vendor-facing preview,
  // so only non-NC POs fall back to it.
  const buyerNotesText = po.buyer_notes || (po.source !== 'nc' ? po.notes : '')

  // Inline styles on every cell — html2canvas does not reliably read CSS classes
  // from injected stylesheets; inline styles are always applied.
  const ROW_BG  = '#ffffff'
  const ROW_ALT = '#F9FAFB'
  const TD = (extra = '', idx = 0) =>
    `style="padding:13px 16px;border-bottom:1px solid #E5E7EB;vertical-align:middle;` +
    `line-height:1.5;font-size:12.5px;color:#111827;background:${idx % 2 === 1 ? ROW_ALT : ROW_BG};${extra}"`
  const TH = (extra = '') =>
    `style="padding:11px 16px;text-align:left;font-size:10.5px;font-weight:700;` +
    `text-transform:uppercase;letter-spacing:0.6px;color:#6B7280;` +
    `border-bottom:2px solid #E5E7EB;background:#F3F4F6;white-space:nowrap;${extra}"`

  const lineRows = po.line_items.map((item, i) => `
    <tr>
      <td ${TD('text-align:center;width:40px;', i)}>${i + 1}</td>
      <td ${TD('', i)}>${escHtml(item.description)}</td>
      ${hasMaterial ? `<td ${TD('font-family:Courier New,monospace;font-size:11px;', i)}>${escHtml(item.material_id ?? '—')}</td>` : ''}
      <td ${TD('text-align:right;font-family:Courier New,monospace;width:80px;', i)}>${item.qty}</td>
      <td ${TD('width:85px;', i)}>${escHtml(item.unit)}</td>
      ${hasSample ? `<td ${TD('width:90px;', i)}>${escHtml(item.sample ?? '—')}</td>` : ''}
      <td ${TD('text-align:right;font-family:Courier New,monospace;width:120px;', i)}>${formatAmount(item.unit_price, po.currency)}</td>
      <td ${TD('text-align:right;font-family:Courier New,monospace;font-weight:600;width:120px;', i)}>${formatAmount(item.line_total, po.currency)}</td>
    </tr>
  `).join('')

  // tax_rate is serialized as a JSON string (Decimal) — coerce so numeric-key lookups hit.
  const taxRate = Number(po.tax_rate)
  const taxLabel = TAX_LABELS[taxRate] ?? `Tax (${Math.round(taxRate * 100)}%)`

  const logoHtml = tpl.show_logo && config.logo_data_url
    ? `<img src="${config.logo_data_url}" alt="Logo" class="logo" />`
    : `<div class="logo-text">${escHtml(config.name)}</div>`

  // Resolve delivery address: PO value → company default → fallback text
  const deliveryAddr = po.delivery_address || config.delivery_address || 'To be confirmed'

  const headerNoteHtml = tpl.header_note
    ? `<div style="text-align:center;font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#6B7280;margin-bottom:20px;">${escHtml(tpl.header_note)}</div>`
    : ''

  const footerNoteHtml = tpl.footer_note
    ? `<div style="margin-top:8px;font-size:11px;color:#6B7280;text-align:center;">${escHtml(tpl.footer_note)}</div>`
    : ''

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Purchase Order ${escHtml(po.number)}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: #111827;
    background: #fff;
    padding: 40px;
    max-width: 900px;
    margin: 0 auto;
  }
  @media print {
    body { padding: 20px; }
    .no-print { display: none; }
    @page { margin: 1.5cm; }
  }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; }
  .logo { max-height: 56px; max-width: 180px; object-fit: contain; }
  .logo-text { font-size: 22px; font-weight: 800; color: #085E5E; letter-spacing: -0.5px; }
  .company-info { text-align: right; }
  .company-info .company-name { font-size: 16px; font-weight: 700; color: #111827; }
  .company-info .tagline { font-size: 11px; color: #6B7280; margin-top: 2px; }
  .po-title {
    background: #085E5E;
    color: white;
    padding: 16px 24px;
    border-radius: 8px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .po-title h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }
  .po-title .status { font-size: 12px; background: rgba(255,255,255,0.2); padding: 4px 10px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
  .meta-block { border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 16px; }
  .meta-block h3 { font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; color: #6B7280; font-weight: 600; margin-bottom: 8px; }
  .meta-row { display: flex; gap: 8px; margin-bottom: 5px; font-size: 12.5px; }
  .meta-row .label { color: #6B7280; min-width: 130px; flex-shrink: 0; }
  .meta-row .value { color: #111827; font-weight: 500; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 4px; font-size: 12.5px; table-layout: fixed; }
  thead tr { background: #F3F4F6; }
  th { padding: 11px 14px; text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #6B7280; border-bottom: 2px solid #E5E7EB; white-space: nowrap; overflow: hidden; }
  td { padding: 11px 14px; border-bottom: 1px solid #F3F4F6; vertical-align: middle; line-height: 1.5; word-break: break-word; }
  tr.alt td { background: #F9FAFB; }
  .right { text-align: right; }
  .center { text-align: center; }
  .mono { font-family: 'Courier New', monospace; }
  .bold { font-weight: 600; }
  .totals { display: flex; justify-content: flex-end; margin-top: 8px; }
  .totals-box { border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 18px; min-width: 260px; }
  .totals-row { display: flex; justify-content: space-between; gap: 24px; margin-bottom: 6px; font-size: 12.5px; color: #374151; }
  .totals-row .amount { font-family: 'Courier New', monospace; }
  .totals-row.total { border-top: 2px solid #E5E7EB; padding-top: 8px; margin-top: 4px; font-size: 14px; font-weight: 700; color: #111827; }
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #E5E7EB; font-size: 11px; color: #9CA3AF; text-align: center; line-height: 1.6; }
  .terms { margin-top: 24px; background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 16px; }
  .terms h3 { font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; color: #6B7280; font-weight: 600; margin-bottom: 8px; }
  .terms p { font-size: 12px; color: #374151; line-height: 1.6; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div>${logoHtml}</div>
  <div class="company-info">
    <div class="company-name">${escHtml(config.name)}</div>
    ${config.tagline ? `<div class="tagline">${escHtml(config.tagline)}</div>` : ''}
  </div>
</div>

<!-- PO Title Bar -->
<div class="po-title">
  <div>
    <h1>Purchase Order</h1>
    <div style="font-size:13px;margin-top:4px;opacity:0.85">${escHtml(po.number)}</div>
  </div>
  <div class="status">${escHtml(po.status.replace('_', ' '))}</div>
</div>

${headerNoteHtml}

<!-- Meta grid -->
<div class="meta-grid">
  <div class="meta-block">
    <h3>Order Details</h3>
    <div class="meta-row"><span class="label">PO Number</span><span class="value">${escHtml(po.number)}</span></div>
    <div class="meta-row"><span class="label">PO Date</span><span class="value">${formatDate(po.created_at)}</span></div>
    <div class="meta-row"><span class="label">Procurement Type</span><span class="value">Type ${po.type} — ${TYPE_LABELS[po.type]}</span></div>
    ${buyerNotesText ? `<div class="meta-row"><span class="label">Notes</span><span class="value">${escHtml(buyerNotesText)}</span></div>` : ''}
  </div>
  <div class="meta-block">
    <h3>Delivery</h3>
    <div class="meta-row"><span class="label">Vendor</span><span class="value">${escHtml(po.vendor_name)}</span></div>
    <div class="meta-row"><span class="label">Expected Delivery</span><span class="value">${po.expected_delivery ? formatDate(po.expected_delivery) : '—'}</span></div>
    <div class="meta-row"><span class="label">Delivery Address</span><span class="value">${escHtml(deliveryAddr)}</span></div>
    ${po.incoterms ? `<div class="meta-row"><span class="label">Incoterms</span><span class="value">${escHtml(po.incoterms)}</span></div>` : ''}
  </div>
</div>

<!-- Line Items -->
<table style="width:100%;border-collapse:collapse;margin-bottom:4px;font-size:12.5px;table-layout:fixed;">
  <thead>
    <tr>
      <th ${TH('text-align:center;width:40px;')}>#</th>
      <th ${TH()}>Description</th>
      ${hasMaterial ? `<th ${TH('width:120px;')}>Material ID</th>` : ''}
      <th ${TH('text-align:right;width:80px;')}>Qty</th>
      <th ${TH('width:85px;')}>Unit</th>
      ${hasSample ? `<th ${TH('width:90px;')}>Sample</th>` : ''}
      <th ${TH('text-align:right;width:120px;')}>Unit Price</th>
      <th ${TH('text-align:right;width:120px;')}>Line Total</th>
    </tr>
  </thead>
  <tbody>
    ${lineRows}
  </tbody>
</table>

<!-- Totals -->
<div class="totals">
  <div class="totals-box">
    <div class="totals-row">
      <span>Subtotal</span>
      <span class="amount">${formatAmount(Number(po.subtotal), po.currency)}</span>
    </div>
    <div class="totals-row">
      <span>${taxLabel}</span>
      <span class="amount">${formatAmount(Number(po.tax_amount), po.currency)}</span>
    </div>
    <div class="totals-row total">
      <span>Total (${escHtml(po.currency)})</span>
      <span class="amount">${formatAmount(Number(po.total), po.currency)}</span>
    </div>
  </div>
</div>

${tpl.show_terms ? `<!-- Terms -->
<div class="terms">
  <h3>Terms &amp; Conditions</h3>
  <p>${escHtml(tpl.terms_text || 'Payment terms: Net 30 days from invoice date. Please confirm receipt of this Purchase Order and advise of any issues with availability or delivery dates. Reference the PO number on all correspondence and invoices.')}</p>
</div>` : footerNoteHtml}

<div class="footer">
  Generated by ${escHtml(config.name)} · ${formatDate(new Date().toISOString())}
  &nbsp;|&nbsp; This document is system-generated and valid without a physical signature.
</div>

</body>
</html>`
}

function escHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
