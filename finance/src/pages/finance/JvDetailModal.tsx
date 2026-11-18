/**
 * Journal voucher detail modal — header, dual-currency lines, dimensions and
 * lifecycle actions (review / unreview / post / unpost / reverse). Shared by
 * the Journal Vouchers page and the Account Balance / Budget Actual drills.
 * Styling follows GeneralLedgerPage (neutral palette, #085E5E primary).
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Check, Loader2, RotateCcw, Send, Undo2 } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn, formatDate, formatDateTime } from '@/lib/utils'

import { TBody, TFoot, THead, Table, Td, Th, Tr, Modal as ShellModal, Button } from '@uniops/shell'

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
  source_subsystem?: string | null; source_subsystem_label?: string | null
  // NC header facts. Names, not ids: NC's SM_USER accounts are not UniOps users,
  // so there is nothing to link to — the point is to show what NC shows.
  nc_num?: number | null
  nc_prepared_name?: string | null; nc_checked_name?: string | null
  nc_manager_name?: string | null; nc_voucher_type_name?: string | null
  nc_attachment_count?: number | null
  nc_voucher_state?: string | null
  nc_voucher_kind?: number | null; nc_voucher_kind_label?: string | null
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
  /** In NC's Aux. Acctg order; `display` is "code/name" as NC prints it
   *  (the code alone when UniOps holds no name for it). */
  dims: { dim_code: string; value_text: string | null; label: string; display: string }[]
}
interface JvDetail { voucher: JvHeader; lines: JvLine[] }

export const JV_STATUS_STYLE: Record<string, string> = {
  draft: 'bg-surface-sunken text-fg-secondary',
  reviewed: 'bg-info-soft text-info-fg',
  posted: 'bg-success-soft text-success-fg',
  reversed: 'bg-danger-soft text-danger-fg',
}

export function JvStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize',
      JV_STATUS_STYLE[status] ?? 'bg-surface-sunken text-fg-secondary')}>
      {status}
    </span>
  )
}

function Stamp({ label, name, at }: { label: string; name?: string | null; at?: string | null }) {
  return (
    <div className="text-xs text-fg-muted">
      <span className="font-medium text-fg-secondary">{label}:</span>{' '}
      {name || '—'}{at ? ` · ${formatDateTime(at)}` : ''}
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

  // `stacked`: this modal is opened FROM list overlays (AccountVouchersModal,
  // UnallocatedLinesModal) and has to sit above the one that launched it. Both
  // portal to document.body, so mount order alone would decide the winner; the
  // explicit higher layer says what is meant: the voucher is the child overlay.
  return (
    <ShellModal open onClose={onClose} title={<span className="inline-flex items-center gap-2">{v ? `${v.voucher_word} · ${v.jv_number}` : 'Journal Voucher'}{v && <JvStatusBadge status={v.status} />}</span>} icon={<BookOpen className="h-4 w-4" />} closeOnBackdrop closeOnEscape={false} stacked size="full" className="max-w-6xl">
      {isLoading && <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>}

      {v && (
        <>
          <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-lg bg-surface-subtle px-4 py-3 md:grid-cols-3">
            <div className="text-xs text-fg-muted"><span className="font-medium text-fg-secondary">Date:</span> {formatDate(v.voucher_date)} · {v.fiscal_period}</div>
            <div className="text-xs text-fg-muted md:col-span-2"><span className="font-medium text-fg-secondary">Summary:</span> {v.summary || '—'}</div>
            <div className="text-xs text-fg-muted md:col-span-3">
              <span className="font-medium text-fg-secondary">Source:</span>{' '}
              {v.source_doc_type ? `${v.source_doc_type} · ${v.source_doc_number ?? v.source_doc_id}` : (v.nc_source_pk ? `NC65 import · ${v.nc_source_pk}` : '—')}
            </div>
            {/* NC-sourced vouchers have NO UniOps lifecycle stamps — their
                status mirrors NC's tally and the crud verbs reject any
                hand-change — so showing three empty ones was all this said
                about 40k+ vouchers. Show NC's own 制单/审核/记账 instead. */}
            {v.nc_source_pk ? (
              <>
                <Stamp label="Prepared (NC)" name={v.nc_prepared_name} />
                <Stamp label="Checked (NC)" name={v.nc_checked_name} />
                <Stamp label="Posted (NC)" name={v.nc_manager_name} />
              </>
            ) : (
              <>
                <Stamp label="Prepared" name={v.prepared_by_name} at={v.prepared_at} />
                <Stamp label="Reviewed" name={v.reviewed_by_name} at={v.reviewed_at} />
                <Stamp label="Posted" name={v.posted_by_name} at={v.posted_at} />
              </>
            )}
            {(v.nc_voucher_type_name || v.nc_voucher_kind_label
              || (v.nc_voucher_state && v.nc_voucher_state !== 'normal')) && (
              <div className="text-xs text-fg-muted md:col-span-3">
                <span className="font-medium text-fg-secondary">NC:</span>{' '}
                {[v.nc_voucher_type_name,
                  v.nc_num != null ? `No. ${v.nc_num}` : null,
                  v.nc_voucher_kind_label,
                  v.nc_voucher_state && v.nc_voucher_state !== 'normal'
                    ? v.nc_voucher_state.toUpperCase() : null,
                  v.nc_attachment_count ? `${v.nc_attachment_count} attachment(s)` : null,
                ].filter(Boolean).join(' · ')}
              </div>
            )}
          </div>

          {err && <div className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{err}</div>}

          <div className="overflow-x-auto rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr>
                  <Th className="w-8">#</Th>
                  <Th>Account</Th>
                  <Th>Summary</Th>
                  <Th>Aux. Acctg</Th>
                  {multiCurrency && <Th align="right" className="w-24 ">Orig Dr</Th>}
                  {multiCurrency && <Th align="right" className="w-24 ">Orig Cr</Th>}
                  <Th align="right" className="w-28 ">Debit (CAD)</Th>
                  <Th align="right" className="w-28 ">Credit (CAD)</Th>
                </Tr>
              </THead>
              <TBody>
                {data.lines.map((l, i) => (
                  <Tr key={l.line_no} className={cn('border-t border-line-subtle align-top', i % 2 && 'bg-surface-subtle/40')}>
                    <Td className="font-mono text-xs text-fg-muted">{l.line_no}</Td>
                    <Td>
                      <span className="font-mono text-xs">{l.account_code ?? '—'}</span>
                      {l.account_name && <span className="ml-1 text-fg-secondary">{l.account_name}</span>}
                    </Td>
                    <Td>
                      {l.summary && <div className="text-fg-secondary">{l.summary}</div>}
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {/* UniOps' own mapping — only for lines NC gave no
                            auxiliaries (internal vouchers); on NC lines the
                            Aux. Acctg column already says it, in NC's terms. */}
                        {l.dims.length === 0 && l.cost_center_code && <Dim label={`CC ${l.cost_center_code}`} title={l.cost_center_name} />}
                        {l.dims.length === 0 && l.department_code && <Dim label={`Dept ${l.department_code}`} title={l.department_name} />}
                        {l.dims.length === 0 && l.partner_name && <Dim label={l.partner_name} />}
                        {l.tax_code && <Dim label={`Tax ${l.tax_code}`} />}
                        {l.currency !== 'CAD' && <Dim label={`${l.currency} @ ${l.fx_rate}`} />}
                        {l.quantity && <Dim label={`${l.quantity} ${l.unit ?? ''} @ ${l.price ?? ''}`} />}
                      </div>
                    </Td>
                    {/* Reads like NC's own Aux. Acctg cell:
                        【Department: 0106/Engineering】【Cost Center: E04】 */}
                    <Td className="text-xs text-fg-secondary">
                      {l.dims.length === 0 ? <span className="text-fg-faint">—</span>
                        : l.dims.map((d, j) => (
                          <span key={j} className="mr-1 inline-block whitespace-nowrap">
                            【<span className="text-fg-muted">{d.label}:</span> {d.display}】
                          </span>
                        ))}
                    </Td>
                    {multiCurrency && <Td align="right" className="font-mono">{money(l.orig_debit)}</Td>}
                    {multiCurrency && <Td align="right" className="font-mono">{money(l.orig_credit)}</Td>}
                    <Td align="right" className="font-mono">{money(l.local_debit)}</Td>
                    <Td align="right" className="font-mono">{money(l.local_credit)}</Td>
                  </Tr>
                ))}
              </TBody>
              <TFoot>
                <Tr className="border-t-2 bg-surface-subtle font-semibold">
                  <Td colSpan={multiCurrency ? 6 : 4}>Totals (CAD)</Td>
                  <Td align="right" className="font-mono">{money(v.total_local_debit)}</Td>
                  <Td align="right" className="font-mono">{money(v.total_local_credit)}</Td>
                </Tr>
              </TFoot>
            </Table>
          </div>

          {canAct && (
            <div className="mt-4 flex justify-end gap-2 border-t border-line-subtle pt-3">
              {v.status === 'draft' && !v.nc_source_pk && (
                <Button size="md" onClick={() => act('review')} disabled={!!busy}>
                  {busy === 'review' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Review
                </Button>
              )}
              {v.status === 'reviewed' && (
                <>
                  <Button variant="secondary" size="md" onClick={() => act('unreview')} disabled={!!busy}>
                    {busy === 'unreview' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unreview
                  </Button>
                  <Button size="md" onClick={() => act('post')} disabled={!!busy}>
                    {busy === 'post' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Post
                  </Button>
                </>
              )}
              {v.status === 'posted' && !v.nc_source_pk && (
                <>
                  <Button variant="secondary" size="md" onClick={() => act('unpost')} disabled={!!busy}>
                    {busy === 'unpost' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unpost
                  </Button>
                  <Button variant="danger-outline" size="md"
                    onClick={() => act('reverse', 'Create a posted red-flush voucher that offsets this one, and mark it reversed?')}
                    disabled={!!busy} >
                    {busy === 'reverse' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />} Reverse
                  </Button>
                </>
              )}
            </div>
          )}
        </>
      )}
    </ShellModal>
  )
}

function Dim({ label, title }: { label: string; title?: string | null }) {
  return (
    <span title={title ?? undefined}
          className="inline-flex rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-fg-secondary">
      {label}
    </span>
  )
}
