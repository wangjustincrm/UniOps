/**
 * Journal voucher detail modal — header, dual-currency lines, dimensions and
 * lifecycle actions (review / unreview / post / unpost / reverse). Shared by
 * the Journal Vouchers page and the Account Balance / Budget Actual drills.
 * Styling follows GeneralLedgerPage (neutral palette, #085E5E primary).
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Check, Loader2, RotateCcw, Send, Undo2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const dangerBtn = 'flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50'

function money(v: string | null | undefined) {
  const n = Number(v ?? 0)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}

export interface JvHeader {
  id: string; jv_number: string; voucher_word: string; voucher_date: string
  fiscal_period: string; summary: string | null; status: string
  source_service?: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
  total_debit: string; total_credit: string
  total_local_debit: string; total_local_credit: string
  reverses_jv_id: string | null; reversed_by_jv_id: string | null
  prepared_by_name?: string | null; prepared_at?: string | null
  reviewed_by_name?: string | null; reviewed_at?: string | null
  posted_by_name?: string | null; posted_at?: string | null
  nc_source_pk?: string | null
}
interface JvLine {
  line_no: number; account_code: string | null; account_name: string | null
  summary: string | null
  orig_debit: string; orig_credit: string; local_debit: string; local_credit: string
  currency: string; fx_rate: string
  quantity: string | null; unit: string | null; price: string | null
  cost_center_code: string | null; cost_center_name: string | null
  department_code: string | null; department_name: string | null
  partner_name: string | null; tax_code: string | null
  dims: { dim_code: string; value_text: string | null }[]
}
interface JvDetail { voucher: JvHeader; lines: JvLine[] }

export const JV_STATUS_STYLE: Record<string, string> = {
  draft: 'bg-neutral-100 text-neutral-600',
  reviewed: 'bg-blue-50 text-blue-700',
  posted: 'bg-green-50 text-green-700',
  reversed: 'bg-red-50 text-red-700',
}

export function JvStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize',
      JV_STATUS_STYLE[status] ?? 'bg-neutral-100 text-neutral-600')}>
      {status}
    </span>
  )
}

function Stamp({ label, name, at }: { label: string; name?: string | null; at?: string | null }) {
  return (
    <div className="text-xs text-neutral-500">
      <span className="font-medium text-neutral-600">{label}:</span>{' '}
      {name || '—'}{at ? ` · ${at.slice(0, 16).replace('T', ' ')}` : ''}
    </div>
  )
}

export function JvDetailModal({ jvId, canAct, onClose, onActed }: {
  jvId: string; canAct: boolean; onClose: () => void; onActed?: () => void
}) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['jv-detail', jvId],
    queryFn: () => financeApi.get<JvDetail>(`/journal-vouchers/${jvId}`),
  })

  const act = async (action: string, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(action); setErr(null)
    try {
      await financeApi.post(`/journal-vouchers/${jvId}/${action}`, {})
      await qc.invalidateQueries({ queryKey: ['jv-detail', jvId] })
      onActed?.()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const v = data?.voucher
  const multiCurrency = data?.lines.some((l) => l.currency !== 'CAD') ?? false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <BookOpen className="h-4 w-4 text-neutral-400" />
            {v ? `${v.voucher_word} · ${v.jv_number}` : 'Journal Voucher'}
            {v && <JvStatusBadge status={v.status} />}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading && <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>}

        {v && (
          <>
            <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-lg bg-neutral-50 px-4 py-3 md:grid-cols-3">
              <div className="text-xs text-neutral-500"><span className="font-medium text-neutral-600">Date:</span> {v.voucher_date} · {v.fiscal_period}</div>
              <div className="text-xs text-neutral-500 md:col-span-2"><span className="font-medium text-neutral-600">Summary:</span> {v.summary || '—'}</div>
              <div className="text-xs text-neutral-500 md:col-span-3">
                <span className="font-medium text-neutral-600">Source:</span>{' '}
                {v.source_doc_type ? `${v.source_doc_type} · ${v.source_doc_number ?? v.source_doc_id}` : (v.nc_source_pk ? `NC65 import · ${v.nc_source_pk}` : '—')}
              </div>
              <Stamp label="Prepared" name={v.prepared_by_name} at={v.prepared_at} />
              <Stamp label="Reviewed" name={v.reviewed_by_name} at={v.reviewed_at} />
              <Stamp label="Posted" name={v.posted_by_name} at={v.posted_at} />
            </div>

            {err && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2 w-8">#</th>
                    <th className="px-3 py-2">Account</th>
                    <th className="px-3 py-2">Summary / Dimensions</th>
                    {multiCurrency && <th className="px-3 py-2 w-24 text-right">Orig Dr</th>}
                    {multiCurrency && <th className="px-3 py-2 w-24 text-right">Orig Cr</th>}
                    <th className="px-3 py-2 w-28 text-right">Debit (CAD)</th>
                    <th className="px-3 py-2 w-28 text-right">Credit (CAD)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((l, i) => (
                    <tr key={l.line_no} className={cn('border-t border-neutral-100 align-top', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-400">{l.line_no}</td>
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs">{l.account_code ?? '—'}</span>
                        {l.account_name && <span className="ml-1 text-neutral-600">{l.account_name}</span>}
                      </td>
                      <td className="px-3 py-2">
                        {l.summary && <div className="text-neutral-700">{l.summary}</div>}
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {l.cost_center_code && <Dim label={`CC ${l.cost_center_code}`} title={l.cost_center_name} />}
                          {l.department_code && <Dim label={`Dept ${l.department_code}`} title={l.department_name} />}
                          {l.partner_name && <Dim label={l.partner_name} />}
                          {l.tax_code && <Dim label={`Tax ${l.tax_code}`} />}
                          {l.dims.map((d, j) => <Dim key={j} label={`${d.dim_code}: ${d.value_text ?? ''}`} />)}
                          {l.currency !== 'CAD' && <Dim label={`${l.currency} @ ${l.fx_rate}`} />}
                          {l.quantity && <Dim label={`${l.quantity} ${l.unit ?? ''} @ ${l.price ?? ''}`} />}
                        </div>
                      </td>
                      {multiCurrency && <td className="px-3 py-2 text-right font-mono">{money(l.orig_debit)}</td>}
                      {multiCurrency && <td className="px-3 py-2 text-right font-mono">{money(l.orig_credit)}</td>}
                      <td className="px-3 py-2 text-right font-mono">{money(l.local_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(l.local_credit)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
                    <td className="px-3 py-2" colSpan={multiCurrency ? 5 : 3}>Totals (CAD)</td>
                    <td className="px-3 py-2 text-right font-mono">{money(v.total_local_debit)}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(v.total_local_credit)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>

            {canAct && (
              <div className="mt-4 flex justify-end gap-2 border-t border-neutral-100 pt-3">
                {v.status === 'draft' && !v.nc_source_pk && (
                  <button onClick={() => act('review')} disabled={!!busy} className={primaryBtn}>
                    {busy === 'review' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Review
                  </button>
                )}
                {v.status === 'reviewed' && (
                  <>
                    <button onClick={() => act('unreview')} disabled={!!busy} className={secondaryBtn}>
                      {busy === 'unreview' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unreview
                    </button>
                    <button onClick={() => act('post')} disabled={!!busy} className={primaryBtn}>
                      {busy === 'post' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Post
                    </button>
                  </>
                )}
                {v.status === 'posted' && !v.nc_source_pk && (
                  <>
                    <button onClick={() => act('unpost')} disabled={!!busy} className={secondaryBtn}>
                      {busy === 'unpost' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unpost
                    </button>
                    <button
                      onClick={() => act('reverse', 'Create a posted red-flush voucher that offsets this one, and mark it reversed?')}
                      disabled={!!busy} className={dangerBtn}>
                      {busy === 'reverse' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />} Reverse
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Dim({ label, title }: { label: string; title?: string | null }) {
  return (
    <span title={title ?? undefined}
          className="inline-flex rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600">
      {label}
    </span>
  )
}
