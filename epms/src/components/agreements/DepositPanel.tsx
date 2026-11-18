import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CreditCard, RotateCcw, Undo2, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { StatusBadge } from '@/components/ui/badge'
import { useCloseOnEscape } from '@/hooks/useCloseOnEscape'
import { useCreatePa, usePas, useRecordDepositRefund, useReturnDepositRefund, useConfirmSettlement } from '@/hooks/usePas'
import { paService, type ApiPa } from '@/services/pa'
import type { ApiAgreement } from '@/services/agreement'
import { errorMessage, rememberSubmitFailure } from '@/lib/submitFailure'
import { ATTACHMENT_RULE, partitionUploads } from '@/lib/uploadLimits'
import { formatAmount, formatDate, formatBytes } from '@/lib/utils'
import type { DocumentStatus } from '@/types'

/**
 * OneTime (Deposit) agreements (user decisions 2026-11-17):
 *   - one deposit PA, a prepayment whose amount is the agreement's deposit —
 *     no invoice, the agreement's attachments are its evidence;
 *   - once paid, the refund is recorded on it (0 .. deposit, evidence
 *     required, reason when less came back) and Finance confirms or returns it.
 * The server enforces every rule (epms-api pa.py _apply_deposit_pa_rules /
 * record_deposit_refund); this file only explains them and offers the actions.
 */

const DEAD = ['cancelled', 'rejected']

/** A deposit PA = the prepayment raised on a deposit agreement. */
export function isDepositPa(pa: Pick<ApiPa, 'pa_type' | 'agreement_id'>): boolean {
  return pa.pa_type === 'prepayment' && !!pa.agreement_id
}

/** A deposit refund = a settlement carrying deposit_refund_amount. */
export function isDepositRefund(pa: Pick<ApiPa, 'deposit_refund_amount'>): boolean {
  return pa.deposit_refund_amount !== null && pa.deposit_refund_amount !== undefined
}

const FINANCE_ROLES = ['finance_bp', 'finance_manager', 'finance_director', 'system_admin']

/** Mirrors pa.py _DEPOSIT_REFUND_CONFIRMERS (primary or additional role). */
export function canConfirmDepositRefund(role: string | undefined, extraRoles: string[] | undefined): boolean {
  return [role ?? '', ...(extraRoles ?? [])].some((r) => FINANCE_ROLES.includes(r))
}

// ── Agreement Detail: the Deposit card ───────────────────────────────────────

export function DepositCard({ agreement, attachmentCount, canCreatePaPerm, admissible }: {
  agreement: ApiAgreement
  attachmentCount: number
  canCreatePaPerm: boolean
  admissible: boolean
}) {
  // GET /pa has no agreement_id filter — narrow on the number, then re-filter
  // on the real column (same approach as PaCreatePage's agreement mode).
  const { data: pasData } = usePas({ search: agreement.number })
  const mine = (pasData?.items ?? []).filter((p) => p.agreement_id === agreement.id)
  const deposits = mine.filter((p) => p.pa_type === 'prepayment')
  const liveDeposit = deposits.find((p) => !DEAD.includes(p.status))
  const refunds = liveDeposit
    ? mine.filter((p) => p.pa_type === 'settlement' && p.prepayment_pa_id === liveDeposit.id)
    : []
  const [creating, setCreating] = useState(false)
  const [recording, setRecording] = useState(false)

  const blocked =
    !admissible ? 'The agreement must be active (or in its grace window) to raise its deposit PA.'
    : attachmentCount === 0 ? 'Upload the signed agreement or deposit request under Attachments first — it is the deposit PA’s only evidence.'
    : null
  const canRefund = !!liveDeposit && liveDeposit.status === 'processed'
    && liveDeposit.settlement_status === 'pending'
    && !refunds.some((r) => !DEAD.includes(r.status))

  return (
    <div className="rounded-xl bg-surface shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-muted">Deposit</h2>
        <div className="flex gap-2">
          {!liveDeposit && canCreatePaPerm && (
            <span title={blocked ?? undefined}>
              <Button size="sm" disabled={!!blocked} onClick={() => setCreating(true)}>
                <CreditCard className="h-3.5 w-3.5" />
                Create Deposit PA
              </Button>
            </span>
          )}
          {canRefund && canCreatePaPerm && (
            <Button size="sm" variant="secondary" onClick={() => setRecording(true)}>
              <RotateCcw className="h-3.5 w-3.5" />
              Record Refund
            </Button>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-3 text-sm">
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs font-medium text-fg-muted">Deposit Amount</dt>
          <dd className="font-semibold text-fg">
            {agreement.deposit_amount ? formatAmount(Number(agreement.deposit_amount), agreement.currency) : '—'}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs font-medium text-fg-muted">Deposit PA</dt>
          <dd className="text-fg">
            {liveDeposit ? (
              <span className="inline-flex items-center gap-2">
                <Link to={`/pa/${liveDeposit.id}`} className="font-medium text-primary-700 hover:underline">{liveDeposit.pa_number}</Link>
                <StatusBadge status={liveDeposit.status as DocumentStatus} />
              </span>
            ) : 'Not raised yet'}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs font-medium text-fg-muted">Refund</dt>
          <dd className="text-fg">{refundSummary(liveDeposit, refunds, agreement.currency)}</dd>
        </div>
      </dl>

      {refunds.length > 0 && (
        <div className="rounded-lg border border-line overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line bg-surface-subtle text-xs font-semibold text-fg-muted">
                <th className="px-4 py-2 text-left">Refund PA</th>
                <th className="px-4 py-2 text-right">Refunded</th>
                <th className="px-4 py-2 text-right">Retained</th>
                <th className="px-4 py-2 text-left">Reason</th>
                <th className="px-4 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((r) => {
                const refunded = Number(r.deposit_refund_amount ?? 0)
                const retained = Number(liveDeposit?.payment_amount ?? 0) - refunded
                return (
                  <tr key={r.id} className="border-b border-line-subtle last:border-0">
                    <td className="px-4 py-2"><Link to={`/pa/${r.id}`} className="font-medium text-primary-700 hover:underline">{r.pa_number}</Link></td>
                    <td className="px-4 py-2 text-right tabular-nums">{formatAmount(refunded, agreement.currency)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{retained > 0 ? formatAmount(retained, agreement.currency) : '—'}</td>
                    <td className="px-4 py-2 text-fg-secondary">{r.deposit_retained_reason || '—'}</td>
                    <td className="px-4 py-2"><StatusBadge status={r.status as DocumentStatus} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {creating && <CreateDepositPaDialog agreement={agreement} onClose={() => setCreating(false)} />}
      {recording && liveDeposit && <RecordRefundDialog deposit={liveDeposit} onClose={() => setRecording(false)} />}
    </div>
  )
}

function refundSummary(deposit: ApiPa | undefined, refunds: ApiPa[], currency: string): string {
  if (!deposit) return '—'
  if (deposit.settlement_status === 'settled') {
    const done = refunds.find((r) => r.status === 'processed')
    return done ? `Refunded ${formatAmount(Number(done.deposit_refund_amount ?? 0), currency)} — confirmed` : 'Settled'
  }
  if (refunds.some((r) => r.status === 'submitted')) return 'Recorded — waiting for Finance to confirm'
  if (deposit.status !== 'processed') return 'After the deposit is paid'
  return 'Not refunded yet'
}

// ── Shared dialog shell ──────────────────────────────────────────────────────

function DialogShell({ title, subtitle, onClose, children }: {
  title: string
  subtitle?: string
  onClose: () => void
  children: React.ReactNode
}) {
  useCloseOnEscape(true, onClose)
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-inverse/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl bg-surface shadow-2xl max-h-[90vh] overflow-y-auto" role="dialog" aria-modal="true">
        <div className="flex items-center justify-between border-b border-line-subtle px-6 py-4">
          <div>
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
            {subtitle && <p className="text-xs text-fg-muted">{subtitle}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-fg-muted hover:bg-surface-sunken hover:text-fg-secondary">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

// ── Create Deposit PA ────────────────────────────────────────────────────────

function CreateDepositPaDialog({ agreement, onClose }: { agreement: ApiAgreement; onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const createPa = useCreatePa()
  const [title, setTitle] = useState(`Deposit — ${agreement.title}`.slice(0, 255))
  const [expectedRefund, setExpectedRefund] = useState(agreement.valid_to)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const amount = Number(agreement.deposit_amount ?? 0)

  const submit = async () => {
    if (busy || !title.trim()) return
    setBusy(true)
    setError(null)
    let created: ApiPa
    try {
      // The server sets the amount from the agreement whatever is sent here.
      created = await createPa.mutateAsync({
        title: title.trim(),
        agreement_id: agreement.id,
        vendor_id: agreement.vendor_id,
        vendor_name: agreement.vendor_name,
        pa_type: 'prepayment',
        subtotal: amount,
        tax_amount: 0,
        shipping_amount: 0,
        other_charges: 0,
        currency: agreement.currency,
        invoice_ids: [],
        line_items: [],
        expected_settlement_date: expectedRefund || undefined,
        notes: notes.trim() || undefined,
      })
    } catch (err) {
      setError(errorMessage(err, 'The deposit PA could not be created.'))
      setBusy(false)
      return
    }
    // Created → submit. A refused submit still leaves the draft, which is
    // where the operator is taken, with the reason (same as New PA).
    try {
      await paService.action(created.id, { action: 'submit' })
    } catch (err) {
      rememberSubmitFailure(
        created.id,
        `Deposit PA created, but it could not be submitted for approval: ${errorMessage(err, 'Unknown error')} ` +
        'It has been saved as a draft — use Submit to try again.',
      )
    }
    // Fire-and-forget: awaiting it waits for every open ['pas'] query in every
    // kept-alive tab (a full PA list pages through everything), which left the
    // dialog on "Submitting…" after the PA was already created and submitted.
    void queryClient.invalidateQueries({ queryKey: ['pas'] })
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    // Tabs stay mounted: close the dialog before leaving, or this tab still
    // shows it, busy, when the user comes back.
    setBusy(false)
    onClose()
    navigate(`/pa/${created.id}`)
  }

  return (
    <DialogShell title="Create Deposit PA" subtitle={`${agreement.number} · ${agreement.vendor_name}`} onClose={onClose}>
      <div className="rounded-lg border border-primary-100 bg-primary-50/60 px-3 py-2 text-xs text-primary-800">
        A prepayment of the agreement’s deposit, with no invoice — the agreement’s attachments are its
        evidence. It goes through the normal PA approval and Finance pays it. When the vendor returns the
        money, record the refund on the deposit PA.
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-fg-muted">Amount (from the agreement, cannot be changed)</span>
        <span className="text-lg font-semibold text-fg">{formatAmount(amount, agreement.currency)}</span>
      </div>
      <FormField label="Title" required htmlFor="depositTitle">
        <Input id="depositTitle" value={title} maxLength={255} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} />
      </FormField>
      <FormField label="Expected Refund Date" htmlFor="depositRefundDate" hint="Usually the agreement's end date.">
        <input
          id="depositRefundDate"
          type="date"
          value={expectedRefund}
          onChange={(e) => setExpectedRefund(e.target.value)}
          className="h-10 w-full rounded-md border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>
      <FormField label="Notes" htmlFor="depositNotes">
        <textarea
          id="depositNotes"
          rows={3}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full resize-none rounded-md border border-line-strong bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>
      {error && <p role="alert" className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button size="sm" onClick={submit} disabled={busy || !title.trim()}>
          <CreditCard className="h-3.5 w-3.5" />
          {busy ? 'Submitting…' : 'Create & Submit'}
        </Button>
      </div>
    </DialogShell>
  )
}

// ── Record Refund ────────────────────────────────────────────────────────────

export function RecordRefundDialog({ deposit, onClose, onRecorded }: {
  deposit: ApiPa
  onClose: () => void
  onRecorded?: (refund: ApiPa) => void
}) {
  const navigate = useNavigate()
  const record = useRecordDepositRefund(deposit.id)
  const depositAmount = Number(deposit.payment_amount)
  const [amount, setAmount] = useState(String(depositAmount.toFixed(2)))
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refund = Number(amount)
  const amountValid = amount.trim() !== '' && Number.isFinite(refund) && refund >= 0 && refund <= depositAmount + 1e-9
  const retained = amountValid ? Math.max(depositAmount - refund, 0) : 0
  const needsReason = amountValid && retained >= 0.005
  const canSubmit = amountValid && files.length > 0 && (!needsReason || reason.trim() !== '') && !record.isPending

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const { ok, problems } = partitionUploads(Array.from(list), ATTACHMENT_RULE)
    setFileProblem(problems.length ? problems.join(' ') : null)
    setFiles((prev) => [...prev, ...ok])
  }

  const submit = () => {
    if (!canSubmit) return
    setError(null)
    record.mutate(
      {
        refund_amount: refund.toFixed(2),
        retained_reason: needsReason ? reason.trim() : undefined,
        notes: notes.trim() || undefined,
        files,
      },
      {
        onSuccess: (created) => {
          // Close first — tabs stay mounted (see CreateDepositPaDialog).
          onClose()
          if (onRecorded) onRecorded(created)
          else navigate(`/pa/${created.id}`)
        },
        onError: (err) => setError(errorMessage(err, 'The refund could not be recorded.')),
      },
    )
  }

  return (
    <DialogShell title="Record Deposit Refund" subtitle={`${deposit.pa_number} · ${deposit.vendor_name}`} onClose={onClose}>
      <div className="rounded-lg border border-primary-100 bg-primary-50/60 px-3 py-2 text-xs text-primary-800">
        Record what the vendor actually returned. Nothing is paid — Finance confirms the money has arrived,
        then the deposit is settled. The refund itself is booked in NC by Finance.
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-fg-muted">Deposit paid</span>
        <span className="text-base font-semibold text-fg">{formatAmount(depositAmount, deposit.currency)}</span>
      </div>
      <FormField
        label="Amount Refunded"
        required
        htmlFor="refundAmount"
        error={amount.trim() !== '' && !amountValid ? `Between 0 and ${formatAmount(depositAmount, deposit.currency)}` : undefined}
      >
        <Input
          id="refundAmount"
          type="number"
          min={0}
          max={depositAmount}
          step="0.01"
          value={amount}
          error={amount.trim() !== '' && !amountValid}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAmount(e.target.value)}
        />
      </FormField>
      {needsReason && (
        <FormField
          label={`Reason ${formatAmount(retained, deposit.currency)} was retained`}
          required
          htmlFor="retainedReason"
        >
          <textarea
            id="retainedReason"
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. repair of floor damage deducted per clause 12"
            className="w-full resize-none rounded-md border border-line-strong bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
        </FormField>
      )}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-fg-secondary">Refund Evidence <span className="text-danger-fg">*</span></span>
        <label
          htmlFor="refund-evidence"
          className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed border-line-strong p-4 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
        >
          <Upload className="h-6 w-6 text-fg-muted" />
          <span className="text-sm text-fg-secondary">Bank receipt, vendor’s refund letter…</span>
          <span className="text-xs text-fg-muted">{ATTACHMENT_RULE.hint}</span>
          <input id="refund-evidence" type="file" multiple accept={ATTACHMENT_RULE.accept} className="sr-only"
            onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
        </label>
        {fileProblem && <p role="alert" className="text-xs text-danger-fg">{fileProblem}</p>}
        {files.map((f, i) => (
          <div key={i} className="flex items-center gap-2 rounded-md border border-line bg-surface-subtle px-3 py-1.5 text-sm">
            <span className="flex-1 truncate text-fg-secondary">📎 {f.name}</span>
            <span className="text-fg-muted text-xs">{formatBytes(f.size)}</span>
            <button type="button" aria-label={`Remove ${f.name}`} className="text-fg-muted hover:text-danger-fg"
              onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}>
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      <FormField label="Notes" htmlFor="refundNotes">
        <textarea
          id="refundNotes"
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full resize-none rounded-md border border-line-strong bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
      </FormField>
      {error && <p role="alert" className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={record.isPending}>Cancel</Button>
        <Button size="sm" onClick={submit} disabled={!canSubmit}>
          <RotateCcw className="h-3.5 w-3.5" />
          {record.isPending ? 'Recording…' : 'Record Refund'}
        </Button>
      </div>
    </DialogShell>
  )
}

// ── PA Detail: refund summary + Finance actions ──────────────────────────────

export function DepositRefundPanel({ pa, depositAmount, canFinance }: {
  pa: ApiPa
  /** The deposit PA's paid amount, when loaded. */
  depositAmount: number | null
  canFinance: boolean
}) {
  const confirm = useConfirmSettlement(pa.id)
  const ret = useReturnDepositRefund(pa.id)
  const [returning, setReturning] = useState(false)
  const [comment, setComment] = useState('')
  const refunded = Number(pa.deposit_refund_amount ?? 0)
  const retained = depositAmount !== null ? depositAmount - refunded : null
  const busy = confirm.isPending || ret.isPending
  const err = confirm.error ?? ret.error

  return (
    <div className="rounded-xl border border-primary-100 bg-primary-50/40 p-5 flex flex-col gap-4">
      <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wide">Deposit Refund</h3>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3 text-sm">
        <div><dt className="text-xs text-fg-muted">Deposit</dt>
          <dd className="font-medium">{depositAmount !== null ? formatAmount(depositAmount, pa.currency) : '—'}</dd></div>
        <div><dt className="text-xs text-fg-muted">Refunded</dt>
          <dd className="font-semibold text-fg">{formatAmount(refunded, pa.currency)}</dd></div>
        <div><dt className="text-xs text-fg-muted">Retained by vendor</dt>
          <dd className="font-medium">{retained !== null && retained > 0.004 ? formatAmount(retained, pa.currency) : '—'}</dd></div>
        {pa.deposit_retained_reason && (
          <div className="sm:col-span-3"><dt className="text-xs text-fg-muted">Reason retained</dt>
            <dd className="whitespace-pre-wrap text-fg">{pa.deposit_retained_reason}</dd></div>
        )}
      </dl>
      {pa.status === 'submitted' && (
        <p className="text-sm text-fg-secondary">
          Waiting for Finance to confirm the refund has been received (evidence under Attachments).
          {pa.expected_settlement_date ? ` Expected ${formatDate(pa.expected_settlement_date)}.` : ''}
        </p>
      )}
      {pa.status === 'submitted' && canFinance && (
        <div className="flex flex-col gap-2">
          {returning ? (
            <>
              <textarea
                rows={2}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Why is this refund being returned? (not received yet, wrong amount, evidence missing…)"
                className="w-full resize-none rounded-md border border-line-strong bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => setReturning(false)}>Cancel</Button>
                <Button size="sm" disabled={busy || !comment.trim()} onClick={() => ret.mutate(comment.trim())}>
                  <Undo2 className="h-3.5 w-3.5" />
                  {ret.isPending ? 'Returning…' : 'Return Refund'}
                </Button>
              </div>
            </>
          ) : (
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => confirm.mutate()}>
                <CheckCircle2 className="h-3.5 w-3.5" />
                {confirm.isPending ? 'Confirming…' : 'Confirm Refund Received'}
              </Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => setReturning(true)}>
                <Undo2 className="h-3.5 w-3.5" />
                Return
              </Button>
            </div>
          )}
        </div>
      )}
      {err && <p role="alert" className="text-sm text-danger-fg">{errorMessage(err, 'The action failed')}</p>}
    </div>
  )
}
