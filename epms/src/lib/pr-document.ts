import type { PrRecord } from '@/stores/pr.store'
import type { CompanySettings } from '@/stores/company.store'
import { formatAmount, formatDate } from '@/lib/utils'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Materials / Packaging',
  2: 'Consumables / Misc',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project-Related',
}

export function generatePrHtml(pr: PrRecord, settings: CompanySettings): string {
  const tpl = settings.pdfTemplates?.pr ?? { showLogo: true, headerNote: '', footerNote: '', showTerms: false }
  const hasMaterial = pr.type === 1 || pr.type === 3

  const ROW_BG  = '#ffffff'
  const ROW_ALT = '#F9FAFB'
  const TD = (extra = '', idx = 0) =>
    `style="padding:13px 16px;border-bottom:1px solid #E5E7EB;vertical-align:middle;` +
    `line-height:1.5;font-size:12.5px;color:#111827;background:${idx % 2 === 1 ? ROW_ALT : ROW_BG};${extra}"`
  const TH = (extra = '') =>
    `style="padding:11px 16px;text-align:left;font-size:10.5px;font-weight:700;` +
    `text-transform:uppercase;letter-spacing:0.6px;color:#6B7280;` +
    `border-bottom:2px solid #E5E7EB;background:#F3F4F6;white-space:nowrap;${extra}"`

  const lineRows = pr.lineItems.map((item, i) => `
    <tr>
      <td ${TD('text-align:center;width:40px;', i)}>${i + 1}</td>
      <td ${TD('', i)}>${escHtml(item.description)}</td>
      ${hasMaterial ? `<td ${TD('font-family:Courier New,monospace;font-size:11px;', i)}>${escHtml(item.materialId ?? '—')}</td>` : ''}
      <td ${TD('text-align:right;font-family:Courier New,monospace;width:80px;', i)}>${item.qty}</td>
      <td ${TD('width:85px;', i)}>${escHtml(item.unit)}</td>
      <td ${TD('text-align:right;font-family:Courier New,monospace;width:120px;', i)}>${formatAmount(item.unitPrice, pr.currency ?? 'CAD')}</td>
      <td ${TD('text-align:right;font-family:Courier New,monospace;font-weight:600;width:120px;', i)}>${formatAmount(item.lineTotal, pr.currency ?? 'CAD')}</td>
    </tr>
  `).join('')

  const logoHtml = tpl.showLogo && settings.logoDataUrl
    ? `<img src="${settings.logoDataUrl}" alt="Logo" class="logo" />`
    : `<div class="logo-text">${escHtml(settings.name)}</div>`

  const deliveryAddr = pr.deliveryAddress || settings.deliveryAddress || 'Not specified'

  const headerNoteHtml = tpl.headerNote
    ? `<div class="header-note">${escHtml(tpl.headerNote)}</div>`
    : ''

  const termsHtml = tpl.showTerms
    ? `<div class="terms">
        <h3>Notes &amp; Terms</h3>
        <p>${escHtml(tpl.termsText || 'This Purchase Requisition is a formal internal request and does not constitute a binding commitment to the vendor until a Purchase Order is issued. Please quote the PR number in all related correspondence.')}</p>
      </div>`
    : ''

  const footerNoteHtml = tpl.footerNote && !tpl.showTerms
    ? `<div class="footer-note">${escHtml(tpl.footerNote)}</div>`
    : ''

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Purchase Requisition ${escHtml(pr.number)}</title>
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
  .pr-title {
    background: #1D4ED8;
    color: white;
    padding: 16px 24px;
    border-radius: 8px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .pr-title h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }
  .pr-title .status { font-size: 12px; background: rgba(255,255,255,0.2); padding: 4px 10px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
  .header-note { text-align: center; font-size: 11px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; color: #6B7280; margin-bottom: 20px; }
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
  .totals-box { border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 18px; min-width: 240px; }
  .totals-row { display: flex; justify-content: space-between; gap: 24px; margin-bottom: 6px; font-size: 12.5px; color: #374151; }
  .totals-row .amount { font-family: 'Courier New', monospace; }
  .totals-row.total { border-top: 2px solid #E5E7EB; padding-top: 8px; margin-top: 4px; font-size: 14px; font-weight: 700; color: #111827; }
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #E5E7EB; font-size: 11px; color: #9CA3AF; text-align: center; line-height: 1.6; }
  .footer-note { margin-top: 8px; font-size: 11px; color: #6B7280; text-align: center; }
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
    <div class="company-name">${escHtml(settings.name)}</div>
    ${settings.tagline ? `<div class="tagline">${escHtml(settings.tagline)}</div>` : ''}
  </div>
</div>

<!-- PR Title Bar -->
<div class="pr-title">
  <div>
    <h1>Purchase Requisition</h1>
    <div style="font-size:13px;margin-top:4px;opacity:0.85">${escHtml(pr.number)}</div>
  </div>
  <div class="status">${escHtml(pr.status.replace(/_/g, ' '))}</div>
</div>

${headerNoteHtml}

<!-- Meta grid -->
<div class="meta-grid">
  <div class="meta-block">
    <h3>Requisition Details</h3>
    <div class="meta-row"><span class="label">PR Number</span><span class="value">${escHtml(pr.number)}</span></div>
    <div class="meta-row"><span class="label">Submitted Date</span><span class="value">${formatDate(pr.submittedAt)}</span></div>
    <div class="meta-row"><span class="label">Required By</span><span class="value">${formatDate(pr.requiredBy)}</span></div>
    <div class="meta-row"><span class="label">Procurement Type</span><span class="value">Type ${pr.type} — ${TYPE_LABELS[pr.type]}</span></div>
    <div class="meta-row"><span class="label">Budget Code</span><span class="value">${escHtml(pr.budgetCode || '—')}</span></div>
    <div class="meta-row"><span class="label">Currency</span><span class="value">${escHtml(pr.currency ?? 'CAD')}</span></div>
    ${pr.notes ? `<div class="meta-row"><span class="label">Notes</span><span class="value">${escHtml(pr.notes)}</span></div>` : ''}
  </div>
  <div class="meta-block">
    <h3>Vendor &amp; Delivery</h3>
    <div class="meta-row"><span class="label">Preferred Vendor</span><span class="value">${escHtml(pr.vendor || '—')}</span></div>
    <div class="meta-row"><span class="label">Title</span><span class="value">${escHtml(pr.title)}</span></div>
    <div class="meta-row"><span class="label">Delivery Address</span><span class="value">${escHtml(deliveryAddr)}</span></div>
    ${pr.poNumber ? `<div class="meta-row"><span class="label">Linked PO</span><span class="value">${escHtml(pr.poNumber)}</span></div>` : ''}
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
      <th ${TH('text-align:right;width:120px;')}>Unit Price</th>
      <th ${TH('text-align:right;width:120px;')}>Line Total</th>
    </tr>
  </thead>
  <tbody>
    ${lineRows}
  </tbody>
</table>

<!-- Total -->
<div class="totals">
  <div class="totals-box">
    <div class="totals-row total">
      <span>Total (${escHtml(pr.currency ?? 'CAD')})</span>
      <span class="amount">${formatAmount(pr.amount, pr.currency ?? 'CAD')}</span>
    </div>
  </div>
</div>

${termsHtml}
${footerNoteHtml}

<div class="footer">
  Generated by ${escHtml(settings.name)} · ${formatDate(new Date().toISOString())}
  &nbsp;|&nbsp; This is an internal procurement document.
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
