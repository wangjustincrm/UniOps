import type { ApiPo } from '@/services/po'
import type { CompanyConfig } from '@/services/config'
import { formatAmount, formatDate } from '@/lib/utils'

export interface EmailVars {
  po_number: string
  po_date: string
  vendor_name: string
  vendor_email: string
  expected_delivery: string
  delivery_address: string
  line_items: string
  subtotal: string
  tax: string
  total: string
  company_name: string
  sender_name: string
}

export function buildEmailVars(
  po: ApiPo,
  config: CompanyConfig,
  senderName: string,
  vendorEmail: string
): EmailVars {
  const lineItems = po.line_items
    .map((item, i) =>
      `  ${String(i + 1).padStart(2)}. ${item.description.padEnd(36)} ${String(item.qty).padStart(6)} ${item.unit.padEnd(8)} @ ${formatAmount(item.unit_price, po.currency).padStart(12)}  =  ${formatAmount(item.line_total, po.currency).padStart(12)}`
    )
    .join('\n')

  return {
    po_number: po.number,
    po_date: formatDate(po.created_at),
    vendor_name: po.vendor_name,
    vendor_email: vendorEmail,
    expected_delivery: po.expected_delivery ? formatDate(po.expected_delivery) : '—',
    delivery_address: po.delivery_address || config.delivery_address || 'To be confirmed',
    line_items: lineItems,
    subtotal: formatAmount(Number(po.subtotal), po.currency),
    tax: Number(po.tax_rate) > 0
      ? `${formatAmount(Number(po.tax_amount), po.currency)} (${Math.round(Number(po.tax_rate) * 100)}%)`
      : `0% — Exempt`,
    total: formatAmount(Number(po.total), po.currency),
    company_name: config.name,
    sender_name: senderName,
  }
}

export function renderTemplate(template: string, vars: EmailVars): string {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    return (vars as unknown as Record<string, string>)[key] ?? `{${key}}`
  })
}

export const TEMPLATE_VARIABLE_DOCS: Array<{ variable: string; description: string }> = [
  { variable: '{po_number}',        description: 'PO number (e.g. PO-ABC-2603-01)' },
  { variable: '{po_date}',          description: 'PO creation date' },
  { variable: '{vendor_name}',      description: 'Vendor company name' },
  { variable: '{expected_delivery}',description: 'Expected delivery date' },
  { variable: '{delivery_address}', description: 'Delivery address' },
  { variable: '{line_items}',       description: 'Formatted list of ordered items' },
  { variable: '{subtotal}',         description: 'Subtotal before tax' },
  { variable: '{tax}',              description: 'Tax amount and rate' },
  { variable: '{total}',            description: 'Grand total (CAD)' },
  { variable: '{company_name}',     description: 'Your company name' },
  { variable: '{sender_name}',      description: 'Name of the person placing the order' },
]
