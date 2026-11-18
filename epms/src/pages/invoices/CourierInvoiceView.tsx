import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Loader2, RefreshCw, CheckCircle2, RotateCcw, Trash2, CreditCard, AlertTriangle, UserPlus,
  FileText, ExternalLink, Upload, Link2,
} from 'lucide-react'
import { BackLink } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import { useDepartments } from '@/hooks/useDepartments'
import { useCostCenters } from '@/hooks/useCostCenters'
import {
  useAutoMatch, useDeleteFreightInvoice, useFinalizeFreightInvoice, useFreightAccess, useFreightInvoice,
  useFreightInvoiceAttachments, useFreightShipments, useReopenFreightInvoice, useUpdateFreightLine,
  useUploadFreightAttachment,
} from '@/hooks/useFreight'
import {
  freightAttachmentService, type FreightInvoice, type FreightLine, type FreightLineUpdateBody,
} from '@/services/freight'
import {
  CHARGE_TYPE_LABEL, CostCenterSelect, FreightInvoiceStatusBadge, INPUT_SM_CLS, MatchBadge, ProjectBadge, SELECT_SM_CLS,
  TH_CLS, cents, errorMessage, num,
} from '@/pages/freight/freightUi'
import { useStaticTabNav } from '@/hooks/useStaticTabNav'
import { useConfirmDialog } from '@/components/ui/ConfirmDialog'

import { FileInput, Input, Select, TBody, TFoot, THead, Table, Td, Th, Tr, Card } from '@uniops/shell'
/** What would free an invoice from its PA, by the PA's status. Only a draft /
 *  returned PA can be cancelled; one in approval must first be recalled or
 *  returned; an approved / processed one can no longer let go of it (PRD
 *  §action table, PA-APR-004). "Cancel or return that PA" was shown even for an
 *  Approved PA, which offers neither (ISS-0183). */
function paLockHint(status: string | null): string {
  switch (status) {
    case 'draft':
    case 'returned':
      return 'Cancel that PA to change the matching.'
    case 'submitted':
    case 'in_review':
      return 'That PA is in approval: it has to be recalled or returned, then cancelled, before the matching can change.'
    case 'approved':
    case 'processed':
      return `That PA is ${status === 'approved' ? 'approved' : 'processed'}, so the matching can no longer be changed.`
    default:
      return 'Cancel or return that PA to change the matching.'
  }
}

/**
 * A courier invoice (match_route='freight') on its invoice page: the charges,
 * their shipment / cost-centre matching, finalize and Create PA. Rendered by
 * InvoiceDetailPage in place of the PO three-way match. Holders of
 * epms.freight.manage work it; every other invoice reader sees it read-only.
 */
export function CourierInvoiceView({ id }: { id: string }) {
  const { data: inv, isLoading, error: loadError } = useFreightInvoice(id)
  const { openTab, replaceWithTab } = useStaticTabNav()
  const { data: access } = useFreightAccess()
  const canManage = !!access?.manage

  const { data: deptData } = useDepartments()
  const departments = deptData?.items ?? []
  const { data: costCenters = [] } = useCostCenters({ active_only: true })

  const updateLine = useUpdateFreightLine(id)
  const autoMatch = useAutoMatch(id)
  const finalize = useFinalizeFreightInvoice(id)
  const reopen = useReopenFreightInvoice(id)
  const del = useDeleteFreightInvoice(id)
  const [actionError, setActionError] = useState<string | null>(null)

  const confirmDlg = useConfirmDialog()
  const busy = updateLine.isPending || autoMatch.isPending || finalize.isPending || reopen.isPending || del.isPending

  // Pending shipments of this carrier — registered but not on this invoice.
  // Matching: Freight manage, or the person AP assigned it to (server says).
  const canMatch = !!inv?.can_match
  const editable = inv?.status === 'unmatched' && canMatch
  const { data: pendingData } = useFreightShipments(
    { status: 'pending', carrier_id: inv?.carrier_id, page_size: 200 }, !!inv?.carrier_id && editable,
  )
  const pending = pendingData?.items ?? []

  if (isLoading) {
    return <div className="flex justify-center py-24"><Loader2 className="h-6 w-6 animate-spin text-fg-faint" /></div>
  }
  if (!inv) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-fg-muted">{loadError ? errorMessage(loadError, 'Freight invoice not found.') : 'Freight invoice not found.'}</p>
        <BackLink to="/invoices" className="mt-4"><Button variant="secondary">Back to Invoices</Button></BackLink>
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
    confirmDlg.ask({
      title: 'Reopen invoice',
      subtitle: inv.vendor_invoice_number,
      message: 'Reopen this invoice for matching? Its shipments go back to pending until it is finalised again.',
      confirmLabel: 'Reopen',
      tone: 'primary',
      onConfirm: () => reopen.mutateAsync(),
      failureFallback: 'Could not reopen the invoice',
    })
  }
  const handleDelete = () => {
    if (busy) return
    confirmDlg.ask({
      title: 'Delete invoice',
      subtitle: inv.vendor_invoice_number,
      message: `Delete invoice ${inv.vendor_invoice_number}? Its lines are removed; registered shipments stay pending.`,
      confirmLabel: 'Delete',
      onConfirm: async () => {
        await del.mutateAsync()
        replaceWithTab('/invoices')
      },
      failureFallback: 'Could not delete the invoice',
    })
  }

  const ccy = inv.currency
  const due = dueInfo(inv.due_date, inv.status)
  const dueUrgent = due.tone === 'overdue' || due.tone === 'soon'
  const canCreatePa = canManage && inv.status === 'matched' && !inv.pa_id
  const lineNo = new Map(inv.lines.map((l, i) => [l.id, i + 1]))
  const anchors = inv.lines.filter((l) => l.charge_type === 'shipment')
  const amountGap = cents(num(inv.amount) - num(inv.lines_amount_total))
  const taxGap = cents(num(inv.tax_amount) - num(inv.lines_tax_total))

  return (
    <div className="flex flex-col gap-6">
      {confirmDlg.dialog}
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <BackLink to="/invoices" className="p-2 rounded-lg hover:bg-surface-sunken text-fg-muted" aria-label="Back to Invoices">
            <ArrowLeft className="h-5 w-5" />
          </BackLink>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold text-fg">{inv.vendor_name} · {inv.vendor_invoice_number}</h1>
              <FreightInvoiceStatusBadge status={inv.status} />
            </div>
            <p className="text-sm text-fg-muted mt-0.5">
              <span className="font-mono">{inv.internal_ref}</span>
              <span className="mx-2 text-fg-faint">·</span>
              {inv.lines.length} charge{inv.lines.length !== 1 ? 's' : ''}
              {inv.uploaded_by_name && <><span className="mx-2 text-fg-faint">·</span>uploaded by {inv.uploaded_by_name} {formatDateTime(inv.created_at)}</>}
            </p>
          </div>
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg border border-danger-line bg-danger-soft px-4 py-2.5 text-sm text-danger-fg">{actionError}</div>
      )}

      <div className="grid grid-cols-1 gap-6 2xl:grid-cols-4">
        <div className="2xl:col-span-3 min-w-0 flex flex-col gap-5">
          {/* Header card */}
          <Card className="p-5">
            <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm md:grid-cols-4">
              <Field label="Carrier" value={inv.carrier_code ?? '—'} />
              <Field label="Invoice date" value={formatDate(inv.invoice_date)} />
              <Field label="Due date" value={
                <span className={cn(dueUrgent && 'text-danger-fg font-semibold')}>
                  {formatDate(inv.due_date)}{due.label && <span className="ml-1 text-xs">({due.label})</span>}
                </span>} />
              <Field label="Payment application" value={inv.pa_id
                ? <Link to={`/pa/${inv.pa_id}`} className="font-mono text-primary-700 hover:underline">{inv.pa_number}</Link>
                : <span className="text-fg-muted">—</span>} />
              <Field label="Amount before tax" value={<span className="font-mono">{formatAmount(num(inv.amount), ccy)}</span>} />
              <Field label="Tax" value={<span className="font-mono">{formatAmount(num(inv.tax_amount), ccy)}</span>} />
              <Field label="Total" value={<span className="font-mono font-semibold">{formatAmount(num(inv.total_amount), ccy)}</span>} />
              <Field label="Currency" value={ccy} />
            </div>
            {inv.notes && <p className="mt-4 border-t border-line-subtle pt-3 text-sm text-fg-secondary whitespace-pre-wrap">{inv.notes}</p>}
          </Card>

          {/* Lines */}
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-line bg-surface-subtle px-5 py-3">
              <h3 className="text-xs font-semibold text-fg-muted">Charges</h3>
              {editable
                ? <span className="text-xs text-fg-muted">Every change saves at once{updateLine.isPending && ' — saving…'}</span>
                : canManage
                  ? <span className="text-xs text-fg-muted">Reopen the invoice to change the matching</span>
                  : inv.status !== 'unmatched'
                    ? <span className="text-xs text-fg-muted">Matched — the freight manager raises the Freight PA</span>
                    : <span className="text-xs text-fg-muted">Read-only — courier invoices are matched by the freight manager or the person AP assigns</span>}
            </div>
            <div className="overflow-x-auto">
              <Table density="compact">
                <THead>
                  <Tr>
                    {['#', 'Type', 'Tracking No', 'Date', 'Description / Ref', 'Amount', 'Tax', 'Match', 'Cost Center'].map((h) => (
                      <Th key={h} className={cn(TH_CLS, ['Amount', 'Tax'].includes(h) && 'text-right')}>{h}</Th>
                    ))}
                  </Tr>
                </THead>
                <TBody>
                  {inv.lines.map((l, i) => (
                    <LineRow key={l.id} line={l} index={i + 1} inv={inv} editable={editable} busy={busy}
                      anchors={anchors} lineNo={lineNo} pending={pending}
                      costCenters={costCenters} departments={departments} onPatch={patchLine} />
                  ))}
                </TBody>
                <TFoot>
                  <Tr className="border-t-2 bg-surface-subtle text-xs font-semibold">
                    <Td align="right" colSpan={5} className="text-fg-muted">Lines total</Td>
                    <Td className={cn('px-3 py-2 text-right font-mono', amountGap !== 0 && 'text-danger-fg')}>{formatAmount(num(inv.lines_amount_total), ccy)}</Td>
                    <Td className={cn('px-3 py-2 text-right font-mono', taxGap !== 0 && 'text-danger-fg')}>{formatAmount(num(inv.lines_tax_total), ccy)}</Td>
                    <Td colSpan={2} className="text-fg-muted">
                      {(amountGap !== 0 || taxGap !== 0) && 'Does not agree with the invoice header'}
                    </Td>
                  </Tr>
                </TFoot>
              </Table>
            </div>
          </Card>

          {editable && pending.length > 0 && (
            <Card className="p-5">
              <h3 className="text-xs font-semibold text-fg-muted mb-1">Still pending for {inv.carrier_code}</h3>
              <p className="text-xs text-fg-muted mb-3">Registered but not billed on this invoice — usually they come on the next one. Nothing to do here.</p>
              <div className="flex flex-wrap gap-2">
                {pending.map((s) => (
                  <span key={s.id} className="rounded-md border border-line bg-surface-subtle px-2 py-1 text-xs">
                    <span className="font-mono text-fg-secondary">{s.tracking_no}</span>
                    <span className="ml-1.5 text-fg-muted">{s.is_project ? 'Project' : (s.cost_center_code ?? '')} · {formatDate(s.created_at)}</span>
                  </span>
                ))}
              </div>
            </Card>
          )}
        </div>

        {/* Side panel */}
        <div className="order-first grid items-start gap-4 md:grid-cols-3 2xl:order-none 2xl:flex 2xl:flex-col">
          <Card className="p-5 flex flex-col gap-2">
            {inv.match_assignee_name && inv.status === 'unmatched' && (
              <p className="flex items-center gap-1.5 text-xs text-fg-secondary">
                <UserPlus className="h-3.5 w-3.5 text-primary-600" /> Assigned to <span className="font-medium">{inv.match_assignee_name}</span>
              </p>
            )}
            {!canMatch && inv.status === 'unmatched' && (
              <p className="text-xs text-fg-muted">Courier invoice — matched to shipments by the freight manager or the person AP assigns, and paid by the freight manager.</p>
            )}
            {/* By status, not by who may match: finalising closes the
                assignee's task, and with it their right to match. */}
            {!canManage && inv.status === 'matched' && !inv.pa_id && (
              <p className="text-xs text-fg-muted">Matched. The freight manager raises the Freight PA.</p>
            )}
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
              <Button onClick={() => openTab('/pa/new', `source=freight&invoice=${inv.id}`)} className="gap-2">
                <CreditCard className="h-4 w-4" /> Create PA
              </Button>
            )}
            {canManage && inv.status === 'matched' && !inv.pa_id && (
              <Button variant="secondary" onClick={handleReopen} disabled={busy} className="gap-2">
                <RotateCcw className="h-4 w-4" /> {reopen.isPending ? 'Reopening…' : 'Reopen'}
              </Button>
            )}
            {inv.status === 'matched' && inv.pa_id && (
              <p className="text-xs text-fg-muted">This invoice is on <Link to={`/pa/${inv.pa_id}`} className="font-mono text-primary-700 hover:underline">{inv.pa_number}</Link>. {paLockHint(inv.pa_status)}</p>
            )}
            {editable && canManage && (
              <Button variant="danger-ghost" size="sm" onClick={handleDelete} disabled={busy}
                className="mt-1 text-danger-fg">
                <Trash2 className="h-3.5 w-3.5" /> {del.isPending ? 'Deleting…' : 'Delete invoice'}
              </Button>
            )}
          </Card>

          {editable && (
            <div className={cn('rounded-xl border p-5 shadow-sm',
              inv.finalize_blockers.length ? 'border-warning-line bg-warning-soft' : 'border-success-line bg-success-soft')}>
              {inv.finalize_blockers.length === 0 ? (
                <p className="flex items-center gap-2 text-sm font-medium text-success-fg"><CheckCircle2 className="h-4 w-4" /> Ready to finalise</p>
              ) : (
                <>
                  <p className="flex items-center gap-2 text-sm font-medium text-warning-fg mb-2"><AlertTriangle className="h-4 w-4" /> Before it can be finalised</p>
                  <ul className="list-disc pl-5 text-xs text-warning-fg space-y-1">
                    {inv.finalize_blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}

          <Card className="p-5">
            <h3 className="text-xs font-semibold text-fg-muted mb-3">By Cost Center</h3>
            <Table density="compact">
              <TBody>
                {inv.totals_by_cost_center.map((t, i) => (
                  <Tr key={t.cost_center_id ?? `none-${i}`}>
                    <Td>
                      {t.cost_center_id ? (
                        <>
                          <div className="text-fg">{t.cost_center_code} — {t.cost_center_name}</div>
                          <div className="text-2xs text-fg-muted">{t.department_name}</div>
                        </>
                      ) : <span className="font-medium text-warning-fg">No cost center yet</span>}
                    </Td>
                    <Td align="right" className="font-mono align-top">
                      {formatAmount(num(t.amount), ccy)}
                      <div className="text-2xs text-fg-muted">tax {formatAmount(num(t.tax_amount), ccy)}</div>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </Card>

          <InvoicePdfPanel invoiceId={inv.id} />
        </div>
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-fg-muted">{label}</p>
      <div className="font-medium text-fg mt-0.5">{value}</div>
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
  pending: { id: string; tracking_no: string; cost_center_code: string | null; is_project: boolean }[]
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
    l.match_status === 'project' || (l.match_status === 'unregistered' && !l.cost_center_id)
  const followable = anchors.filter((a) => a.id !== l.id)
  const canLinkShipment = editable && (l.charge_type === 'shipment' || l.charge_type === 'adjustment') &&
    !l.shipment_id && pending.length > 0

  return (
    <Tr className={cn('border-b border-line-subtle last:border-0 align-top', needsAttention && editable && 'bg-warning-50/40')}>
      <Td className="text-xs text-fg-muted">{index}</Td>
      <Td className="text-xs text-fg-secondary">{CHARGE_TYPE_LABEL[l.charge_type] ?? l.charge_type}</Td>
      <Td>
        <span className="font-mono text-xs text-fg">{l.tracking_no ?? '—'}</span>
        {others.length > 0 && <div className="font-mono text-2xs text-fg-muted">+ {others.join(', ')}</div>}
      </Td>
      <Td className="text-xs text-fg-secondary whitespace-nowrap">{formatDate(l.ship_date)}</Td>
      <Td className="text-xs max-w-xs">
        <div className="text-fg-secondary">{l.description ?? '—'}</div>
        {l.reference && <div className="text-2xs text-fg-muted">Ref {l.reference}</div>}
      </Td>
      <Td align="right">
        {editable ? (
          <Input type="number" step="0.01" value={amount} disabled={busy}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setAmount(e.target.value)}
            onBlur={(e) => commit('amount', e.target.value)}
            className={cn(INPUT_SM_CLS, 'w-24 text-right font-mono')} />
        ) : <span className="font-mono text-xs">{formatAmount(num(l.amount), ccy)}</span>}
      </Td>
      <Td align="right">
        {editable ? (
          <Input type="number" step="0.01" value={tax} disabled={busy}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTax(e.target.value)}
            onBlur={(e) => commit('tax_amount', e.target.value)}
            className={cn(INPUT_SM_CLS, 'w-20 text-right font-mono')} />
        ) : <span className="font-mono text-xs">{formatAmount(num(l.tax_amount), ccy)}</span>}
      </Td>
      <Td>
        <MatchBadge status={l.match_status} previouslyBilledOn={l.previously_billed_on} />
        {l.linked_line_id && <div className="mt-1 text-2xs text-fg-muted">Follows line #{lineNo.get(l.linked_line_id) ?? '?'}</div>}
        {l.shipment_project_info && (
          <div className="mt-1 max-w-48 text-2xs text-fg-secondary">
            <ProjectBadge className="mr-1" />{l.shipment_project_info}
          </div>
        )}
        {editable && l.match_status === 'project' && (
          <Button variant="link" onClick={() => l.cost_center_id && onPatch(l, { cost_center_id: l.cost_center_id })}
            disabled={busy || !l.cost_center_id}
            title={l.cost_center_id ? undefined : 'Choose the cost center first'}
            className="mt-1 text-2xs">
            {l.cost_center_code ? `Confirm ${l.cost_center_code}` : 'Confirm cost center'}
          </Button>
        )}
        {editable && l.match_status === 'duplicate_billing' && (
          <Button variant="link" onClick={() => l.cost_center_id && onPatch(l, { cost_center_id: l.cost_center_id })}
            disabled={busy || !l.cost_center_id}
            className="mt-1 text-2xs">
            Keep — it is a correct extra charge
          </Button>
        )}
      </Td>
      <Td className="min-w-56">
        {editable ? (
          <div className="flex flex-col gap-1.5">
            <CostCenterSelect
              value={l.cost_center_id ?? ''} disabled={busy}
              onChange={(cc) => cc && onPatch(l, { cost_center_id: cc })}
              costCenters={costCenters} departments={departments}
              className={cn(SELECT_SM_CLS, 'w-full')}
            />
            {l.charge_type === 'pickup' && followable.length > 0 && (
              <Select value={l.linked_line_id ?? ''} disabled={busy}
                onChange={(e) => onPatch(l, e.target.value ? { linked_line_id: e.target.value } : { clear_link: true })}
                className="w-full" selectSize="sm">
                <option value="">Follow shipment… (none)</option>
                {followable.map((a) => (
                  <option key={a.id} value={a.id}>
                    #{lineNo.get(a.id)} {a.tracking_no ?? ''} {a.cost_center_code ? `· ${a.cost_center_code}` : ''}
                  </option>
                ))}
              </Select>
            )}
            {canLinkShipment && (
              <label className="flex items-center gap-1 text-2xs text-fg-muted">
                <Link2 className="h-3 w-3 shrink-0" />
                <Select value="" disabled={busy}
                  onChange={(e) => e.target.value && onPatch(l, { shipment_id: e.target.value })}
                  className="flex-1" selectSize="sm">
                  <option value="">Registered under another number…</option>
                  {pending.map((s) => (
                    <option key={s.id} value={s.id}>{s.tracking_no}{s.is_project ? ' · Project' : s.cost_center_code ? ` · ${s.cost_center_code}` : ''}</option>
                  ))}
                </Select>
              </label>
            )}
          </div>
        ) : (
          <div className="text-xs">
            <div className="text-fg">{l.cost_center_code ? `${l.cost_center_code} — ${l.cost_center_name ?? ''}` : '—'}</div>
            <div className="text-2xs text-fg-muted">{l.department_name ?? ''}</div>
          </div>
        )}
      </Td>
    </Tr>
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
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-fg-muted">Invoice PDF</h3>
        <FileInput ref={input} accept="application/pdf,image/*" className="sr-only" onChange={onFile} />
        <Button variant="secondary" size="xs" onClick={() => input.current?.click()} disabled={upload.isPending}
         >
          <Upload className="h-3 w-3" /> {upload.isPending ? 'Uploading…' : 'Add'}
        </Button>
      </div>
      {isLoading ? (
        <p className="text-xs text-fg-muted">Loading…</p>
      ) : atts.length === 0 ? (
        <p className="text-xs text-fg-muted">No file attached.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {atts.map((a) => (
            <div key={a.id} className="flex items-center gap-2 text-xs">
              <FileText className="h-4 w-4 text-fg-muted shrink-0" />
              <span className="min-w-0 flex-1 truncate text-fg-secondary">{a.file_name}</span>
              <Button variant="secondary" size="xs" onClick={() => freightAttachmentService.open(a.id).catch((x) => setErr(errorMessage(x)))}
               >
                <ExternalLink className="h-3 w-3" /> Open
              </Button>
            </div>
          ))}
        </div>
      )}
      {err && <p className="mt-2 text-xs text-danger-fg">{err}</p>}
    </Card>
  )
}
