/**
 * Remittance advice panel — who will be emailed for a completed payment or
 * batch. Shared by the Payments hub page and the Payment Batches page
 * (Tasks 12/13): both drop in `<RemittancePanel scope={...} />` directly, or
 * via `<RemittanceDialog>`. `RemittanceStatusBadge` is exported here so
 * those pages reuse the same status pill rather than writing a second one —
 * there is no `StatusBadge` in `@uniops/shell`; domain status→variant
 * mapping stays in the app (see `JvStatusBadge` in JvDetailModal.tsx, which
 * this follows).
 *
 * Preview is live: every fetch (including Refresh) recomputes who is
 * sendable from the current state of vendor emails / invoice numbers — it is
 * a plain re-fetch, not a cache bust. Blocked payees cannot be selected; the
 * disabled checkbox is a convenience only, the server refuses them anyway.
 *
 * Sending is two steps: "Preview & Send" renders the selected payees' emails
 * server-side without sending (RemittanceEmailPreview), and only "Confirm &
 * Send" there actually sends — an advice is irreversible once it leaves.
 */
import { Fragment, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Eye, Loader2, RefreshCw } from 'lucide-react'
import { cn, formatDate, todayISO } from '@/lib/utils'
import {
  fetchPreview, renderRemittance, scopeKey, sendRemittance,
  type PayeeGroup, type RemittanceScope, type RenderedEmail, type SendResult,
} from '@/services/remittance'
import { RemittanceEmailPreview } from './RemittanceEmailPreview'

import { Checkbox, Input, TBody, THead, Table, Td, Th, Tr, Button, Badge, type BadgeTone } from '@uniops/shell'
type Recipient = { recipient_kind: string; party_id: string; resend: boolean }

/** What the operator is looking at on the confirmation step, frozen at the
 * moment it was rendered — Confirm sends exactly these recipients with this
 * date, not whatever the selection/date picker says by then. */
type PendingConfirm = { emails: RenderedEmail[]; recipients: Recipient[]; paymentDate: string }

export type RemittanceStatus = 'ready' | 'blocked' | 'sent' | 'failed' | 'skipped' | 'not_sent'

// skipped / not_sent are the lighter grey beside ready → outline (user rule 2026-11-18)
const STATUS_TONE: Record<RemittanceStatus, BadgeTone> = {
  ready: 'neutral',
  blocked: 'warning',
  sent: 'success',
  failed: 'danger',
  skipped: 'outline',
  // Deliberately quieter than 'ready' and a different label: this
  // is the hub list's existence-check state (see `_remittance_status` in
  // finance-api/app/api/v1/payments.py) and must never read as "verified
  // sendable" the way 'ready' does in the live preview below.
  not_sent: 'outline',
}

const STATUS_LABEL: Record<RemittanceStatus, string> = {
  ready: 'Ready', blocked: 'Blocked', sent: 'Sent', failed: 'Failed', skipped: 'Skipped',
  not_sent: 'Not sent',
}

/** Domain status pill for a remittance payee/result. Follows JvStatusBadge's
 * local-badge convention (see JvDetailModal.tsx) rather than a shared
 * `StatusBadge` from `@uniops/shell`, which does not exist. */
export function RemittanceStatusBadge({ status }: { status: RemittanceStatus }) {
  return (
    <Badge variant={STATUS_TONE[status]}>
      {STATUS_LABEL[status]}
    </Badge>
  )
}

const BLOCK_REASON_LABEL: Record<string, string> = {
  missing_email: 'Missing email',
  invalid_email: 'Invalid email address — fix it on the vendor record',
  missing_invoice_no: 'Missing invoice number',
}

function blockReasonText(reasons: string[]): string {
  return reasons.map((r) => BLOCK_REASON_LABEL[r] ?? r).join(', ')
}

function payeeKey(kind: string, id: string): string {
  return `${kind}:${id}`
}

/**
 * Today at the plant (company time zone) as `YYYY-MM-DD` — deliberately not
 * `new Date().toISOString().slice(0, 10)`, which is today in UTC. The
 * company runs at UTC-4/-5, so from 8pm local onwards the UTC form is
 * already tomorrow, and the picker would open on a date the operator never
 * chose (and that the server would reject as future). Same reason the
 * `max` attribute below uses this and not the ISO form.
 */
function todayLocal(): string {
  return todayISO()
}

function fmtMoney(v: string, ccy: string): string {
  return `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy}`
}

function readinessOf(g: PayeeGroup): RemittanceStatus {
  if (g.block_reasons.length > 0) return 'blocked'
  if (g.last_send?.status === 'sent') return 'sent'
  if (g.last_send?.status === 'failed') return 'failed'
  if (g.last_send?.status === 'skipped') return 'skipped'
  return 'ready'
}

export function RemittancePanel({ scope, onSent }: {
  scope: RemittanceScope
  onSent?: (result: SendResult) => void
}) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // Which payee rows are expanded to show their line detail — the lines
  // that will appear in that payee's advice. Default collapsed (empty set)
  // so the panel stays as compact as it was before this existed.
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  // The payment date the advice will SHOW, for every line in it. Defaults to
  // today because that is the common case (advice goes out the day the
  // payment runs), and is backdated by hand when it does not: the funds
  // often leave a day or two before AP gets to the advice — bank cut-off, a
  // cheque run signed the day before — and a date the payee cannot find on
  // their own statement is exactly what this removes. It overrides only what
  // the email displays; the payment record behind the GL is never touched
  // (see finance-api/app/api/v1/remittance.py's PAYMENT_DATE_FIELD).
  const [paymentDate, setPaymentDate] = useState(todayLocal())
  const [sending, setSending] = useState(false)
  const [rendering, setRendering] = useState(false)
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<SendResult | null>(null)

  // Keyed on `scopeKey(scope)`, not `scope.id` — a 'selection' scope has no
  // `.id` of its own, and its key must be stable under reordering of the
  // same set of payment ids (see scopeKey's docstring).
  const key = scopeKey(scope)
  const queryKey = ['remittance-preview', key] as const
  const { data: preview, isLoading, isFetching, error, refetch } = useQuery({
    queryKey,
    queryFn: () => fetchPreview(scope),
  })

  // `preview.groups` from the last render, used only to tell "this payee
  // wasn't in the previous preview" (newly unblocked → default it in) apart
  // from "this payee was here and the user unchecked it" (Refresh must not
  // silently re-check it). Reset to null whenever scope changes so a fresh
  // scope always starts from a clean default rather than diffing against a
  // different scope's payees.
  const prevGroupsRef = useRef<PayeeGroup[] | null>(null)

  // Scope changed (new payment/batch, or a consumer re-pointing this same
  // mounted panel at a different row — see Tasks 12/13) — clear everything
  // that belongs to the old scope rather than relying on the consumer to
  // unmount us. Do not rely on `preview` alone for this: it stays defined
  // (stale) until the new scope's fetch resolves.
  useEffect(() => {
    setSendError(null)
    setLastResult(null)
    setSelected(new Set())
    setExpanded(new Set())
    setPaymentDate(todayLocal())
    setConfirm(null)
    prevGroupsRef.current = null
  }, [key])

  // Preview is recomputed live on every fetch (initial load, Refresh, and
  // the refetch after a Send). Selection rules, in order:
  //  - blocked payees are never selected;
  //  - a payee whose last send already succeeded is never *default*
  //    selected — re-checking it is a deliberate resend, not automatic,
  //    otherwise "3 sent, 2 failed, retry" re-emails the 3 that worked;
  //  - a payee that is new since the previous preview (e.g. just became
  //    unblocked) defaults to selected, so it isn't silently left out;
  //  - a payee that was already present keeps whatever the user last chose
  //    (checked or unchecked) — a plain Refresh must not discard a manual
  //    deselection.
  useEffect(() => {
    if (!preview) return
    const prevGroups = prevGroupsRef.current
    const prevKeys = prevGroups && new Set(prevGroups.map((g) => payeeKey(g.recipient_kind, g.party_id)))

    setSelected((prevSelected) => {
      const next = new Set<string>()
      for (const g of preview.groups) {
        if (g.block_reasons.length > 0) continue
        if (g.last_send?.status === 'sent') continue
        const key = payeeKey(g.recipient_kind, g.party_id)
        if (!prevKeys || !prevKeys.has(key) || prevSelected.has(key)) next.add(key)
      }
      return next
    })

    prevGroupsRef.current = preview.groups
  }, [preview])

  // Step 1 of 2: render the selected payees' emails without sending them.
  async function handlePreview() {
    if (!preview) return
    const selectedGroups = preview.groups.filter((g) => selected.has(payeeKey(g.recipient_kind, g.party_id)))
    if (selectedGroups.length === 0) return
    // Per-payee, not folded with `.some()` into one request-wide flag: only
    // a payee THIS row already shows as sent gets `resend: true` — the
    // checkbox that carries the "Already sent — check to resend" tooltip
    // below. Folding this into a single blanket flag (the Round 1 bug) meant
    // deliberately resending one payee silently waived the duplicate guard
    // for every OTHER payee in the same request too — including one the
    // server independently knows is already sent under this or the other
    // scope (e.g. a colleague resent it a moment ago from the other scope's
    // panel) but that THIS operator never intended to resend. Kept per-payee,
    // that other payee still comes back `skipped`, exactly as it should.
    const recipients: Recipient[] = selectedGroups.map((g) => ({
      recipient_kind: g.recipient_kind,
      party_id: g.party_id,
      resend: readinessOf(g) === 'sent',
    }))
    setRendering(true)
    setSendError(null)
    setLastResult(null)
    try {
      const { emails } = await renderRemittance(scope, recipients, paymentDate)
      setConfirm({ emails, recipients, paymentDate })
    } catch (e) {
      setSendError((e as Error).message)
    } finally {
      setRendering(false)
    }
  }

  // Step 2 of 2: send exactly what was just reviewed. Only the payees the
  // preview showed as `ready` — one it showed as not being sent must not
  // go out because its state changed in between.
  async function handleConfirmSend() {
    if (!confirm) return
    const ready = new Set(confirm.emails.filter((e) => e.status === 'ready')
      .map((e) => payeeKey(e.recipient_kind, e.party_id)))
    const recipients = confirm.recipients.filter((r) => ready.has(payeeKey(r.recipient_kind, r.party_id)))
    if (recipients.length === 0) return
    setSending(true)
    setSendError(null)
    try {
      const result = await sendRemittance(scope, recipients, confirm.paymentDate)
      setConfirm(null)
      setLastResult(result)
      onSent?.(result)
      // Re-fetch rather than locally patch state — a send just changed the
      // last_send row the server reads, and preview is defined to always
      // reflect that live.
      await qc.invalidateQueries({ queryKey })
    } catch (e) {
      setSendError((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (isLoading) {
    return <div className="py-8 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
  }

  if (error) {
    return (
      <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">
        {(error as Error).message || 'Failed to load remittance preview'}
      </div>
    )
  }

  if (!preview) return null

  if (!preview.enabled) {
    return (
      <p className="text-sm text-fg-muted">
        Remittance email is not configured. Ask an administrator to enable it in Company Settings.
      </p>
    )
  }

  const selectedCount = selected.size

  if (confirm) {
    return (
      <div className="space-y-3">
        {sendError && <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{sendError}</div>}
        <RemittanceEmailPreview emails={confirm.emails} sending={sending}
          onBack={() => { setConfirm(null); setSendError(null) }}
          onConfirm={() => void handleConfirmSend()} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-fg-secondary">
          Reference: <span className="font-mono">{preview.reference}</span>
          <span className="mx-2 text-fg-faint">·</span>
          {preview.payment_method}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-fg-secondary">
            Payment date
            <Input inputSize="sm" type="date" value={paymentDate} max={todayLocal()}
              onChange={(e) => setPaymentDate(e.target.value)}
              title="The date the advice tells the payee the funds left. Defaults to today — back-date it if the payment actually cleared earlier."
              />
          </label>
          <Button variant="secondary" size="md" type="button" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {sendError && <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{sendError}</div>}

      {lastResult && (
        <div className="rounded-md bg-surface-subtle px-3 py-2 text-sm text-fg-secondary">
          Sent {lastResult.sent}, failed {lastResult.failed}, skipped {lastResult.skipped}.
          {lastResult.results.filter((r) => r.status === 'failed').map((r) => (
            <div key={payeeKey(r.recipient_kind, r.party_id)} className="text-xs text-danger-fg">
              {r.party_name}: {r.error}
            </div>
          ))}
        </div>
      )}

      {preview.groups.length === 0 ? (
        <p className="py-4 text-center text-sm text-fg-muted">No payees to email for this {scope.kind}.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <Table>
            <THead>
              <Tr>
                <Th className="w-8" />
                <Th>Payee</Th>
                <Th>Email</Th>
                <Th align="right">Amount</Th>
                <Th>Status</Th>
              </Tr>
            </THead>
            <TBody>
              {preview.groups.map((g, i) => {
                const key = payeeKey(g.recipient_kind, g.party_id)
                const blocked = g.block_reasons.length > 0
                const status = readinessOf(g)
                const isExpanded = expanded.has(key)
                return (
                  <Fragment key={key}>
                    <Tr className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40', blocked && 'text-fg-muted')}>
                      <Td>
                        <Checkbox disabled={blocked} checked={selected.has(key)}
                          title={status === 'sent' ? 'Already sent — check to resend' : undefined}
                          onChange={(e) => setSelected((prev) => {
                            const next = new Set(prev)
                            if (e.target.checked) next.add(key)
                            else next.delete(key)
                            return next
                          })} />
                      </Td>
                      <Td>
                        <Button variant="subtle" size="xs" type="button" onClick={() => toggleExpanded(key)}
                          className="mr-1"
                          aria-label={isExpanded ? 'Collapse lines' : 'Expand lines'}>
                          {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                        </Button>
                        {g.party_name}
                        <span className="ml-1 text-xs text-fg-muted">
                          ({g.recipient_kind === 'vendor' ? 'Vendor' : 'Employee'})
                        </span>
                      </Td>
                      <Td className="text-xs text-fg-secondary">
                        {g.email || <span className="text-fg-faint">—</span>}
                      </Td>
                      <Td align="right" className="font-mono">{fmtMoney(g.total, g.currency)}</Td>
                      <Td>
                        <RemittanceStatusBadge status={status} />
                        {blocked && <div className="mt-1 text-xs text-warning-fg">{blockReasonText(g.block_reasons)}</div>}
                        {/* Fix 6 (Round 2): show whenever an error is persisted, not only
                            when status === 'failed'. The backend's CASE refuses to downgrade
                            a row from `sent` back to `failed` (a failed RESEND must not undo
                            an already-delivered advice) but it still overwrites `error` — so a
                            failed resend leaves status: 'sent' with an error message attached.
                            Gating on status alone hid that message entirely, and the panel
                            read as a clean "Sent" with nothing wrong. Wording says the advice
                            already went out — this is not a "your payee never got it" alarm. */}
                        {!blocked && g.last_send?.error && (
                          <div className="mt-1 text-xs text-danger-fg">
                            {status === 'sent'
                              ? `Advice was delivered earlier, but the latest resend attempt failed: ${g.last_send.error}`
                              : g.last_send.error}
                          </div>
                        )}
                      </Td>
                    </Tr>
                    {/* Line detail — what this payee's advice will actually say. Collapsed by
                        default (see `expanded` state above); a line with credit netted off it
                        shows the same three-tier breakdown as the email (gross, one row per
                        credit note naming the vendor's own number, then net) so AP is checking
                        the same thing the vendor will read — see remittance_template.py's
                        `_amount_cell`, whose "less credit {number} {amount}" wording this
                        mirrors. A line with no credit renders as a single amount, same as
                        today. */}
                    {isExpanded && (
                      <Tr className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                        <Td />
                        <Td colSpan={4}>
                          <Table>
                            <TBody>
                              {g.lines.map((l, li) => {
                                const hasCredit = Number(l.credit_applied) > 0
                                return (
                                  <Tr key={li} className={cn(li > 0 && 'border-t border-line-subtle')}>
                                    <Td className="pr-3 align-top text-fg-secondary">{l.reference}</Td>
                                    {/* The date the advice will carry, not the date recorded
                                        against the payment — those differ whenever the operator
                                        back-dates above, and this detail exists so AP is reading
                                        exactly what the payee will read. */}
                                    <Td className="pr-3 align-top text-fg-muted">{formatDate(paymentDate || l.payment_date)}</Td>
                                    <Td align="right" className="align-top font-mono">
                                      {hasCredit ? (
                                        <div className="space-y-0.5">
                                          <div>{fmtMoney(l.gross, g.currency)}</div>
                                          {l.credit_notes.map((note, ci) => (
                                            <div key={ci} className="text-fg-muted">
                                              less credit {note.vendor_credit_number} {fmtMoney(note.applied_amount, g.currency)}
                                            </div>
                                          ))}
                                          <div className="font-semibold">{fmtMoney(l.amount, g.currency)}</div>
                                        </div>
                                      ) : (
                                        fmtMoney(l.amount, g.currency)
                                      )}
                                    </Td>
                                  </Tr>
                                )
                              })}
                            </TBody>
                          </Table>
                        </Td>
                      </Tr>
                    )}
                  </Fragment>
                )
              })}
            </TBody>
          </Table>
        </div>
      )}

      <div className="flex justify-end">
        <Button size="md" type="button" disabled={rendering || selectedCount === 0} onClick={() => void handlePreview()}>
          {rendering ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
          {rendering ? 'Preparing preview…' : `Preview & Send (${selectedCount})`}
        </Button>
      </div>
    </div>
  )
}
