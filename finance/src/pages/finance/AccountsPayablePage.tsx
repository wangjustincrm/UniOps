/**
 * Accounts Payable workbench (Portal Finance)
 *
 * Read-only view of finance-owned AP invoices (the source of truth that EPMS
 * purchase invoices + OA uploaded invoices sync into) plus AP aging.
 * - Invoices: filter by source (EPMS/OA) and status; shows total/paid/outstanding.
 * - Aging: vendor × currency, 5 buckets, outstanding-based (by due date).
 *
 * AP invoices are created/matched in EPMS/OA and paid via Payment Applications —
 * this page is a finance-side viewer, not an editor. Styling follows
 * AccountsReceivablePage / CoaConfigPage.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useTabStoreApi } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, EPMS_URL, OA_URL, encodeSession } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

interface ApInvoice {
  id: string; ap_invoice_number: string; source: string
  source_invoice_id: string; source_ref: string | null
  vendor_id: string | null; vendor_name: string | null; vendor_invoice_number: string | null
  amount: string; tax_amount: string; total_amount: string; paid_amount: string
  currency: string; invoice_date: string; due_date: string | null
  status: string; source_status: string | null
  po_id: string | null; po_number: string | null
}
interface AgingRow {
  vendor_id: string | null; vendor_name: string; currency: string
  current: string; d1_30: string; d31_60: string; d61_90: string; d90_plus: string; total: string
}

const STATUSES = ['draft', 'posted', 'partially_paid', 'paid', 'void'] as const
const SOURCES = ['epms', 'oa'] as const

function fmtMoney(v: string, ccy?: string): string {
  const s = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}
function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: 'bg-neutral-100 text-neutral-600', posted: 'bg-blue-50 text-blue-700',
    partially_paid: 'bg-amber-50 text-amber-700', paid: 'bg-green-50 text-green-700',
    void: 'bg-neutral-100 text-neutral-400',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-neutral-100 text-neutral-600')}>{status.replace('_', ' ')}</span>
}

export default function AccountsPayablePage() {
  const { user, token, refreshToken } = useAuthStore()
  const [tab, setTab] = useState<'invoices' | 'aging'>('invoices')
  const [source, setSource] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const { data: invoices = [], isFetching } = useQuery({
    queryKey: ['ap-invoices', source, statusFilter],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: '1000' })
      if (source) qs.set('source', source)
      if (statusFilter) qs.set('status', statusFilter)
      return financeApi.get<ApInvoice[]>(`/ap/invoices?${qs.toString()}`)
    },
  })
  const { data: aging = [] } = useQuery({
    queryKey: ['ap-aging'],
    queryFn: () => financeApi.get<AgingRow[]>('/ap/aging'),
    enabled: tab === 'aging',
  })

  // Deep-link a row to the source system's invoice detail (with session handoff).
  // EPMS detail route: /invoices/:id ; OA detail route: /invoices/:source/:id
  const session = token && user ? encodeSession(token, refreshToken ?? '', user) : ''
  const detailHref = (inv: ApInvoice): string => {
    const base = inv.source === 'oa' ? OA_URL : EPMS_URL
    const path = inv.source === 'oa'
      ? `/invoices/oa/${inv.source_invoice_id}`
      : `/invoices/${inv.source_invoice_id}`
    return session ? `${base}${path}#__session=${session}` : `${base}${path}`
  }

  // Open the source-system invoice detail as an iframe tab inside Finance (the
  // embedded EPMS/OA page hides its own chrome), instead of a full-page jump.
  const tabApi = useTabStoreApi()
  const openInvoiceTab = (inv: ApInvoice) => {
    tabApi.getState().openTab({
      key: `ext:invoice:${inv.source}:${inv.source_invoice_id}`,
      title: inv.vendor_invoice_number ?? inv.ap_invoice_number,
      kind: 'iframe',
      src: detailHref(inv),
      icon: 'FileText',
      closable: true,
    })
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap"
      title="Accounts Payable"
      subtitle="Vendor invoices (EPMS + OA), owned by Finance, and aging"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          {([['invoices', 'Invoices'], ['aging', 'Aging']] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn('rounded-lg px-4 py-2 text-sm font-medium',
                      tab === t ? 'bg-[#085E5E] text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>

        {tab === 'invoices' ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select value={source} onChange={(e) => setSource(e.target.value)} className={cn(inputCls, 'w-40')}>
                <option value="">All sources</option>
                {SOURCES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
              </select>
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={cn(inputCls, 'w-44')}>
                <option value="">All statuses</option>
                {STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
              </select>
            </div>

            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2 w-40">AP No.</th>
                    <th className="px-3 py-2 w-16">Source</th>
                    <th className="px-3 py-2">Vendor</th>
                    <th className="px-3 py-2 w-32">Vendor Inv #</th>
                    <th className="px-3 py-2 w-24">Issue</th>
                    <th className="px-3 py-2 w-24">Due</th>
                    <th className="px-3 py-2 w-32 text-right">Total</th>
                    <th className="px-3 py-2 w-32 text-right">Outstanding</th>
                    <th className="px-3 py-2 w-28">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {isFetching && (
                    <tr><td colSpan={9} className="px-3 py-6 text-center text-neutral-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
                  )}
                  {!isFetching && invoices.length === 0 && (
                    <tr><td colSpan={9} className="px-3 py-6 text-center text-neutral-400">No AP invoices.</td></tr>
                  )}
                  {invoices.map((inv, i) => {
                    const outstanding = Number(inv.total_amount) - Number(inv.paid_amount)
                    const open = inv.status === 'posted' || inv.status === 'partially_paid'
                    return (
                      <tr key={inv.id}
                          onClick={() => openInvoiceTab(inv)}
                          title="Open invoice detail in a tab"
                          className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                        <td className="px-3 py-2 font-mono text-xs text-[#085E5E] underline-offset-2 hover:underline">{inv.ap_invoice_number}</td>
                        <td className="px-3 py-2 text-xs uppercase text-neutral-500">{inv.source}</td>
                        <td className="px-3 py-2">{inv.vendor_name ?? '—'}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.vendor_invoice_number ?? '—'}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.invoice_date}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.due_date ?? '—'}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtMoney(inv.total_amount, inv.currency)}</td>
                        <td className="px-3 py-2 text-right font-mono">{open ? fmtMoney(String(outstanding)) : '—'}</td>
                        <td className="px-3 py-2"><StatusPill status={inv.status} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2">Vendor</th>
                  <th className="px-3 py-2 w-16">Ccy</th>
                  <th className="px-3 py-2 text-right">Current</th>
                  <th className="px-3 py-2 text-right">1-30</th>
                  <th className="px-3 py-2 text-right">31-60</th>
                  <th className="px-3 py-2 text-right">61-90</th>
                  <th className="px-3 py-2 text-right">90+</th>
                  <th className="px-3 py-2 text-right font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {aging.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No open payables.</td></tr>
                )}
                {aging.map((r, i) => (
                  <tr key={`${r.vendor_id ?? 'none'}-${r.currency}-${i}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2">{r.vendor_name || '—'}</td>
                    <td className="px-3 py-2 text-xs text-neutral-500">{r.currency}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.current)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d1_30)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d31_60)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d61_90)}</td>
                    <td className={cn('px-3 py-2 text-right font-mono', Number(r.d90_plus) > 0 && 'text-red-600')}>{fmtMoney(r.d90_plus)}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold">{fmtMoney(r.total, r.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </PortalChromeLayout>
  )
}
