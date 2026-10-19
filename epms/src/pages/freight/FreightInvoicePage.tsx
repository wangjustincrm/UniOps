import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, Loader2, RefreshCw, CheckCircle2, RotateCcw, Trash2, CreditCard, AlertTriangle,
  FileText, ExternalLink, Upload, Link2,
} from 'lucide-react'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import { useDepartments } from '@/hooks/useDepartments'
import { useCostCenters } from '@/hooks/useCostCenters'
import {
  useAutoMatch, useDeleteFreightInvoice, useFinalizeFreightInvoice, useFreightInvoice,
  useFreightInvoiceAttachments, useFreightShipments, useReopenFreightInvoice, useUpdateFreightLine,
  useUploadFreightAttachment,
} from '@/hooks/useFreight'
import {
  freightAttachmentService, type FreightInvoice, type FreightLine, type FreightLineUpdateBody,
} from '@/services/freight'
import {
  CHARGE_TYPE_LABEL, CostCenterSelect, FreightInvoiceStatusBadge, INPUT_SM_CLS, MatchBadge, SELECT_SM_CLS,
  TH_CLS, cents, errorMessage, num,
} from './freightUi'
import { useFreightNav } from './useFreightNav'

export default function FreightInvoicePage() {
  const { id = '' } = useParams<{ id: string }>()
  const { data: inv, isLoading, error: loadError } = useFreightInvoice(id)
  useDocTabTitle(inv ? `Freight ${inv.vendor_invoice_number}` : undefined)
  const { openFreight, replaceWithFreight } = useFreightNav()

  const { data: deptData } = useDepartments()
  const departments = deptData?.items ?? []
  const { data: costCenters = [] } = useCostCenters({ active_only: true })

  const updateLine = useUpdateFreightLine(id)
  const autoMatch = useAutoMatch(id)
  const finalize = useFinalizeFreightInvoice(id)
  const reopen = useReopenFreightInvoice(id)
  const del = useDeleteFreightInvoice(id)
  const [actionError, setActionError] = useState<string | null>(null)

  const busy = updateLine.isPending || autoMatch.isPending || finalize.isPending || reopen.isPending || del.isPending

  // Pending shipments of this carrier — registered but not on this invoice.
  const editable = inv?.status === 'unmatched'
  const { data: pendingData } = useFreightShipments(
    { status: 'pending', carrier_id: inv?.carrier_id, page_size: 200 }, !!inv?.carrier_id && editable,
  )
  const pending = pendingData?.items ?? []

  if (isLoading) {
    return <div className="flex justify-center py-24"><Loader2 className="h-6 w-6 animate-spin text-neutral-300" /></div>
  }
  if (!inv) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-neutral-500">{loadError ? errorMessage(loadError, 'Freight invoice not found.') : 'Freight invoice not found.'}</p>
        <BackLink to="/freight?tab=invoices" className="mt-4"><Button variant="secondary">Back to Freight</Button></BackLink>
      </div>
    )
  }

  const run = (p: Promise<unknown>, fallback: string) => {
    setActionError(null)
    p.catch((e) => setActionError(errorMessage(e, fallback)))
  }

  const patchLine = (line: FreightLine, body: FreightLineUpdateBody) => {
    if (busy) return
    run(updateLine.mutateAsync({ lineId: line.id, body }), 'Could not update the line')
  }

  const handleAutoMatch = () => { if (!busy) run(autoMatch.mutateAsync(), 'Auto-match failed') }
  const handleFinalize = () => { if (!busy) run(finalize.mutateAsync(), 'Could not finalise the invoice') }
  const handleReopen = () => {
    if (busy) return
    if (!window.confirm('Reopen this invoice for matching? Its shipments go back to pending until it is finalised again.')) return
    run(reopen.mutateAsync(), 'Could not reopen the invoice')
  }
  const handleDelete = () => {
    if (busy) return
    if (!window.confirm(`Delete invoice ${inv.vendor_invoice_number}? Its lines are removed; registered shipments stay pending.`)) return
    setActionError(null)
    del.mutateAsync()
      .then(() => replaceWithFreight('tab=invoices'))
      .catch((e) => setActionError(errorMessage(e, 'Could not delete the invoice')))
  }

  const ccy = inv.currency
  const due = dueInfo(inv.due_date, inv.status)
  const dueUrgent = due.tone === 'overdue' || due.tone === 'soon'
  const canCreatePa = inv.status === 'matched' && !inv.pa_id
  const lineNo = new Map(inv.lines.map((l, i) => [l.id, i + 1]))
  const anchors = inv.lines.filter((l) => l.charge_type === 'shipment')
  const amountGap = cents(num(inv.amount) - num(inv.lines_amount_total))
  const taxGap = cents(num(inv.tax_amount) - num(inv.lines_tax_total))

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <BackLink to="/freight?tab=invoices" className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500" aria-label="Back to Freight">
            <ArrowLeft className="h-5 w-5" />
          </BackLink>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-neutral-900">{inv.vendor_name} · {inv.vendor_invoice_number}</h1>
              <FreightInvoiceStatusBadge status={inv.status} />
            </div>
            <p className="text-sm text-neutral-500 mt-0.5">
              <span className="font-mono">{inv.internal_ref}</span>
              <span className="mx-2 text-neutral-300">·</span>
              {inv.lines.length} charge{inv.lines.length !== 1 ? 's' : ''}
              {inv.uploaded_by_name && <><span className="mx-2 text-neutral-300">·</span>uploaded by {inv.uploaded_by_name} {formatDateTime(inv.created_at)}</>}
            </p>
          </div>
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-2.5 text-sm text-danger-600">{actionError}</div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-4">
        <div className="xl:col-span-3 flex flex-col gap-5">
          {/* Header card */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
            <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm md:grid-cols-4">
              <Field label="Carrier" value={inv.carrier_code ?? '—'} />
              <Field label="Invoice date" value={formatDate(inv.invoice_date)} />
              <Field label="Due date" value={
                <span className={cn(dueUrgent && 'text-danger-600 font-semibold')}>
                  {formatDate(inv.due_date)}{due.label && <span className="ml-1 text-xs">({due.label})</span>}
                </span>} />
              <Field label="Payment application" value={inv.pa_id
                ? <Link to={`/pa/${inv.pa_id}`} className="font-mono text-primary-700 hover:underline">{inv.pa_number}</Link>
                : <span className="text-neutral-400">—</span>} />
              <Field label="Amount before tax" value={<span className="font-mono">{formatAmount(num(inv.amount), ccy)}</span>} />
              <Field label="Tax" value={<span className="font-mono">{formatAmount(num(inv.tax_amount), ccy)}</span>} />
              <Field label="Total" value={<span className="font-mono font-bold">{formatAmount(num(inv.total_amount), ccy)}</span>} />
              <Field label="Currency" value={ccy} />
            </div>
            {inv.notes && <p className="mt-4 border-t border-neutral-100 pt-3 text-sm text-neutral-600 whitespace-pre-wrap">{inv.notes}</p>}
          </div>

          {/* Lines */}
          <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
            <div className="flex items-center justify-between border-b border-neutral-200 bg-neutral-50 px-5 py-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">Charges</h3>
              {editable
                ? <span className="text-xs text-neutral-500">Every change saves at once{updateLine.isPending && ' — saving…'}</span>
                : <span className="text-xs text-neutral-400">Reopen the invoice to change the matching</span>}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-100">
                    {['#', 'Type', 'Tracking No', 'Date', 'Description / Ref', 'Amount', 'Tax', 'Match', 'Cost Center'].map((h) => (
                      <th key={h} className={cn(TH_CLS, ['Amount', 'Tax'].includes(h) && 'text-right')}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {inv.lines.map((l, i) => (
                    <LineRow key={l.id} line={l} index={i + 1} inv={inv} editable={editable} busy={busy}
                      anchors={anchors} lineNo={lineNo} pending={pending}
                      costCenters={costCenters} departments={departments} onPatch={patchLine} />
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-neutral-200 bg-neutral-50 text-xs font-semibold">
                    <td colSpan={5} className="px-3 py-2 text-right text-neutral-500">Lines total</td>
                    <td className={cn('px-3 py-2 text-right font-mono', amountGap !== 0 && 'text-danger-600')}>{formatAmount(num(inv.lines_amount_total), ccy)}</td>
                    <td className={cn('px-3 py-2 text-right font-mono', taxGap !== 0 && 'text-danger-600')}>{formatAmount(num(inv.lines_tax_total), ccy)}</td>
                    <td colSpan={2} className="px-3 py-2 text-neutral-500">
                      {(amountGap !== 0 || taxGap !== 0) && 'Does not agree with the invoice header'}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {editable && pending.length > 0 && (
            <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
              <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-1">Still pending for {inv.carrier_code}</h3>
              <p className="text-xs text-neutral-400 mb-3">Registered but not billed on this invoice — usually they come on the next one. Nothing to do here.</p>
              <div className="flex flex-wrap gap-2">
                {pending.map((s) => (
                  <span key={s.id} className="rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1 text-xs">
                    <span className="font-mono text-neutral-700">{s.tracking_no}</span>
                    <span className="ml-1.5 text-neutral-400">{s.cost_center_code ?? ''} · {formatDate(s.created_at)}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Side panel */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-2">
            {editable && (
              <>
                <Button onClick={handleFinalize} disabled={busy || inv.finalize_blockers.length > 0} className="gap-2">
                  <CheckCircle2 className="h-4 w-4" /> {finalize.isPending ? 'Finalising…' : 'Finalize'}
                </Button>
                <Button variant="secondary" onClick={handleAutoMatch} disabled={busy} className="gap-2">
                  <RefreshCw className={cn('h-4 w-4', autoMatch.isPending && 'animate-spin')} /> Re-run auto-match
                </Button>
              </>
            )}
            {canCreatePa && (
              <Button onClick={() => openFreight(`tab=create-pa&invoice=${inv.id}`)} className="gap-2">
                <CreditCard className="h-4 w-4" /> Create PA
              </Button>
            )}
            {inv.status === 'matched' && !inv.pa_id && (
              <Button variant="secondary" onClick={handleReopen} disabled={busy} className="gap-2">
                <RotateCcw className="h-4 w-4" /> {reopen.isPending ? 'Reopening…' : 'Reopen'}
              </Button>
            )}
            {inv.status === 'matched' && inv.pa_id && (
              <p className="text-xs text-neutral-500">This invoice is on <Link to={`/pa/${inv.pa_id}`} className="font-mono text-primary-700 hover:underline">{inv.pa_number}</Link>. Cancel or return that PA to change the matching.</p>
            )}
            {editable && (
              <button onClick={handleDelete} disabled={busy}
                className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-danger-600 hover:bg-danger-50 disabled:opacity-40">
                <Trash2 className="h-3.5 w-3.5" /> {del.isPending ? 'Deleting…' : 'Delete invoice'}
              </button>
            )}
          </div>

          {editable && (
            <div className={cn('rounded-xl border p-5 shadow-sm',
              inv.finalize_blockers.length ? 'border-warning-200 bg-warning-50' : 'border-success-200 bg-success-50')}>
              {inv.finalize_blockers.length === 0 ? (
                <p className="flex items-center gap-2 text-sm font-medium text-success-700"><CheckCircle2 className="h-4 w-4" /> Ready to finalise</p>
              ) : (
                <>
                  <p className="flex items-center gap-2 text-sm font-medium text-warning-700 mb-2"><AlertTriangle className="h-4 w-4" /> Before it can be finalised</p>
                  <ul className="list-disc pl-5 text-xs text-warning-700 space-y-1">
                    {inv.finalize_blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}

          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
            <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-3">By Cost Center</h3>
            <table className="w-full text-xs">
              <tbody>
                {inv.totals_by_cost_center.map((t, i) => (
                  <tr key={t.cost_center_id ?? `none-${i}`} className="border-b border-neutral-100 last:border-0">
                    <td className="py-1.5">
                      {t.cost_center_id ? (
                        <>
                          <div className="text-neutral-800">{t.cost_center_code} — {t.cost_center_name}</div>
                          <div className="text-[10px] text-neutral-400">{t.department_name}</div>
                        </>
                      ) : <span className="font-medium text-warning-600">No cost center yet</span>}
                    </td>
                    <td className="py-1.5 text-right font-mono align-top">
                      {formatAmount(num(t.amount), ccy)}
                      <div className="text-[10px] text-neutral-400">tax {formatAmount(num(t.tax_amount), ccy)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <InvoicePdfPanel invoiceId={inv.id} />
        </div>
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-neutral-500">{label}</p>
      <div className="font-medium text-neutral-900 mt-0.5">{value}</div>
    </div>
  )
}

// ─── One charge line ──────────────────────────────────────────────────────────

function LineRow({
  line: l, index, inv, editable, busy, anchors, lineNo, pending, costCenters, departments, onPatch,
}: {
  line: FreightLine
  index: number
  inv: FreightInvoice
  editable: boolean
  busy: boolean
  anchors: FreightLine[]
  lineNo: Map<string, number>
  pending: { id: string; tracking_no: string; cost_center_code: string | null }[]
  costCenters: Parameters<typeof CostCenterSelect>[0]['costCenters']
  departments: Parameters<typeof CostCenterSelect>[0]['departments']
  onPatch: (line: FreightLine, body: FreightLineUpdateBody) => void
}) {
  const ccy = inv.currency
  const [amount, setAmount] = useState(String(l.amount))
  const [tax, setTax] = useState(String(l.tax_amount))
  // Re-sync the inline inputs whenever the server's value changes (after any
  // PATCH the whole invoice is re-rendered from the response).
  useEffect(() => { setAmount(String(l.amount)) }, [l.amount])
  useEffect(() => { setTax(String(l.tax_amount)) }, [l.tax_amount])

  const commit = (field: 'amount' | 'tax_amount', raw: string) => {
    const v = Number(raw)
    const cur = num(field === 'amount' ? l.amount : l.tax_amount)
    if (raw.trim() === '' || isNaN(v)) {
      if (field === 'amount') setAmount(String(l.amount)); else setTax(String(l.tax_amount))
      return
    }
    if (cents(v) === cents(cur)) return
    onPatch(l, field === 'amount' ? { amount: v } : { tax_amount: v })
  }

  const others = l.package_tracking_nos.filter((p) => p !== l.tracking_no)
  const needsAttention = l.match_status === 'unmatched' || l.match_status === 'duplicate_billing' ||
    (l.match_status === 'unregistered' && !l.cost_center_id)
  const followable = anchors.filter((a) => a.id !== l.id)
  const canLinkShipment = editable && (l.charge_type === 'shipment' || l.charge_type === 'adjustment') &&
    !l.shipment_id && pending.length > 0

  return (
    <tr className={cn('border-b border-neutral-100 last:border-0 align-top', needsAttention && editable && 'bg-warning-50/40')}>
      <td className="px-3 py-2.5 text-xs text-neutral-400">{index}</td>
      <td className="px-3 py-2.5 text-xs text-neutral-700">{CHARGE_TYPE_LABEL[l.charge_type] ?? l.charge_type}</td>
      <td className="px-3 py-2.5">
        <span className="font-mono text-xs text-neutral-800">{l.tracking_no ?? '—'}</span>
        {others.length > 0 && <div className="font-mono text-[10px] text-neutral-400">+ {others.join(', ')}</div>}
      </td>
      <td className="px-3 py-2.5 text-xs text-neutral-600 whitespace-nowrap">{formatDate(l.ship_date)}</td>
      <td className="px-3 py-2.5 text-xs max-w-xs">
        <div className="text-neutral-700">{l.description ?? '—'}</div>
        {l.reference && <div className="text-[10px] text-neutral-400">Ref {l.reference}</div>}
      </td>
      <td className="px-3 py-2.5 text-right">
        {editable ? (
          <input type="number" step="0.01" value={amount} disabled={busy}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setAmount(e.target.value)}
            onBlur={(e) => commit('amount', e.target.value)}
            className={cn(INPUT_SM_CLS, 'w-24 text-right font-mono')} />
        ) : <span className="font-mono text-xs">{formatAmount(num(l.amount), ccy)}</span>}
      </td>
      <td className="px-3 py-2.5 text-right">
        {editable ? (
          <input type="number" step="0.01" value={tax} disabled={busy}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTax(e.target.value)}
            onBlur={(e) => commit('tax_amount', e.target.value)}
            className={cn(INPUT_SM_CLS, 'w-20 text-right font-mono')} />
        ) : <span className="font-mono text-xs">{formatAmount(num(l.tax_amount), ccy)}</span>}
      </td>
      <td className="px-3 py-2.5">
        <MatchBadge status={l.match_status} previouslyBilledOn={l.previously_billed_on} />
        {l.linked_line_id && <div className="mt-1 text-[10px] text-neutral-500">Follows line #{lineNo.get(l.linked_line_id) ?? '?'}</div>}
        {editable && l.match_status === 'duplicate_billing' && (
          <button onClick={() => l.cost_center_id && onPatch(l, { cost_center_id: l.cost_center_id })}
            disabled={busy || !l.cost_center_id}
            className="mt-1 block text-[11px] font-medium text-primary-700 hover:underline disabled:opacity-40">
            Keep — it is a correct extra charge
          </button>
        )}
      </td>
      <td className="px-3 py-2.5 min-w-56">
        {editable ? (
          <div className="flex flex-col gap-1.5">
            <CostCenterSelect
              value={l.cost_center_id ?? ''} disabled={busy}
              onChange={(cc) => cc && onPatch(l, { cost_center_id: cc })}
              costCenters={costCenters} departments={departments}
              className={cn(SELECT_SM_CLS, 'w-full')}
            />
            {l.charge_type === 'pickup' && followable.length > 0 && (
              <select value={l.linked_line_id ?? ''} disabled={busy}
                onChange={(e) => onPatch(l, e.target.value ? { linked_line_id: e.target.value } : { clear_link: true })}
                className={cn(SELECT_SM_CLS, 'w-full')}>
                <option value="">Follow shipment… (none)</option>
                {followable.map((a) => (
                  <option key={a.id} value={a.id}>
                    #{lineNo.get(a.id)} {a.tracking_no ?? ''} {a.cost_center_code ? `· ${a.cost_center_code}` : ''}
                  </option>
                ))}
              </select>
            )}
            {canLinkShipment && (
              <label className="flex items-center gap-1 text-[10px] text-neutral-500">
                <Link2 className="h-3 w-3 shrink-0" />
                <select value="" disabled={busy}
                  onChange={(e) => e.target.value && onPatch(l, { shipment_id: e.target.value })}
                  className={cn(SELECT_SM_CLS, 'h-7 flex-1 text-[11px]')}>
                  <option value="">Registered under another number…</option>
                  {pending.map((s) => (
                    <option key={s.id} value={s.id}>{s.tracking_no}{s.cost_center_code ? ` · ${s.cost_center_code}` : ''}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
        ) : (
          <div className="text-xs">
            <div className="text-neutral-800">{l.cost_center_code ? `${l.cost_center_code} — ${l.cost_center_name ?? ''}` : '—'}</div>
            <div className="text-[10px] text-neutral-400">{l.department_name ?? ''}</div>
          </div>
        )}
      </td>
    </tr>
  )
}

// ─── Attached PDF ─────────────────────────────────────────────────────────────

function InvoicePdfPanel({ invoiceId }: { invoiceId: string }) {
  const { data: atts = [], isLoading } = useFreightInvoiceAttachments(invoiceId)
  const upload = useUploadFreightAttachment()
  const input = useRef<HTMLInputElement>(null)
  const [err, setErr] = useState<string | null>(null)

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || upload.isPending) return
    setErr(null)
    upload.mutate({ invoiceId, file }, { onError: (x) => setErr(errorMessage(x, 'Upload failed')) })
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide">Invoice PDF</h3>
        <input ref={input} type="file" accept="application/pdf,image/*" className="sr-only" onChange={onFile} />
        <button onClick={() => input.current?.click()} disabled={upload.isPending}
          className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50 disabled:opacity-50">
          <Upload className="h-3 w-3" /> {upload.isPending ? 'Uploading…' : 'Add'}
        </button>
      </div>
      {isLoading ? (
        <p className="text-xs text-neutral-400">Loading…</p>
      ) : atts.length === 0 ? (
        <p className="text-xs text-neutral-400">No file attached.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {atts.map((a) => (
            <div key={a.id} className="flex items-center gap-2 text-xs">
              <FileText className="h-4 w-4 text-neutral-400 shrink-0" />
              <span className="min-w-0 flex-1 truncate text-neutral-700">{a.file_name}</span>
              <button onClick={() => freightAttachmentService.open(a.id).catch((x) => setErr(errorMessage(x)))}
                className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1 text-neutral-600 hover:bg-neutral-50">
                <ExternalLink className="h-3 w-3" /> Open
              </button>
            </div>
          ))}
        </div>
      )}
      {err && <p className="mt-2 text-xs text-danger-600">{err}</p>}
    </div>
  )
}
