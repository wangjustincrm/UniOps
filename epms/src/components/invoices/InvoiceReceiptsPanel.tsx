import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, statusLabel } from '@/components/ui/badge'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useInvoiceAgreementReceipts, useSetInvoiceReceipts, useSettleWithoutReceipt } from '@/hooks/useInvoices'
import type { ApiInvoice } from '@/services/invoices'
import { RECEIPT_TYPE_LABELS, type ApiReceipt, type ReceiptStatus } from '@/services/agreementReceipts'

// Cent-rounded equality — plain float subtraction of two Number()-coerced
// decimal strings can land a hair off zero (e.g. summing several selected
// receipts' totals), which would otherwise make a genuinely-even match look
// like it has a variance. Same convention as MatchPanel's centsEqual (this
// logic was relocated here — Task 6 pulled receipt evidence off /match, and
// Task 8 gives it a permanent home on the invoice detail page instead).
function centsEqual(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100)
}

// One selectable candidate row — multi-select checkbox, mirrors MatchPanel's
// (now-removed) ReceiptCandidateRow. Shows the type badge (Task 5/6 receipts
// are typed — counter slip / delivery note / service sign-off), the
// reference #, the receipt date and its total.
function ReceiptCandidateRow({
  receipt, selected, onToggle, currency,
}: { receipt: ApiReceipt; selected: boolean; onToggle: () => void; currency: string }) {
  return (
    <label
      className={cn(
        'flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors',
        selected ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white hover:bg-neutral-50',
      )}
    >
      <input
        type="checkbox"
        checked={selected}
        onChange={onToggle}
        className="mt-1 h-4 w-4 rounded text-primary-600 focus:ring-primary-500"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-sm font-medium text-neutral-900">
            <Badge variant="neutral">{RECEIPT_TYPE_LABELS[receipt.receipt_type]}</Badge>
            {receipt.receipt_ref ?? '—'}
          </span>
          <span className="shrink-0 text-xs font-medium text-neutral-700">
            {formatAmount(Number(receipt.total_amount), currency)}
          </span>
        </div>
        <p className="text-[11px] text-neutral-400">{formatDate(receipt.receipt_date)}</p>
      </div>
    </label>
  )
}

// The interactive body — only ever mounted once the caller (InvoiceReceiptsPanel
// below) has confirmed the agreement is house_account. Split out so that
// determination can early-return null without running any of this hook logic
// against an agreementId that doesn't actually have receipts.
//
// Fix-round 1 (Minor 1): every amount here — the candidate rows, the
// selected/invoice/difference trio — formats with invoice.currency, not a
// separately-fetched agreement.currency. Two reasons: (1) ApiInvoice.currency
// exists and can genuinely differ from the agreement's, and the invoice-total
// cell was wrong to use the agreement's; (2) receipts don't carry their own
// currency field at all (services/agreementReceipts.ts ApiReceipt has no
// `currency`) — labelling them with the SAME invoice they're being reconciled
// against, rather than fetching the agreement a second time just for its
// currency code, keeps one consistent label across the whole comparison and
// costs no extra request.
function InvoiceReceiptsPanelBody({
  invoice, agreementId,
}: { invoice: ApiInvoice; agreementId: string }) {
  const currency = invoice.currency
  // Task 5's invoice-scoped route — same authorization as match-candidates /
  // agreement-candidates (NOT the agreement detail page's epms.agreement.read
  // gate), so every role that can legitimately match/reconcile this invoice
  // can reach it. No status filter: we need BOTH the open pool or currently-
  // held-by-this-invoice ones (which are 'reconciled', not 'open') — see the
  // client-side filter below. Filtering server-side would need two round
  // trips or a filter the endpoint doesn't have.
  const receiptsQuery = useInvoiceAgreementReceipts(invoice.id, agreementId)
  // Fix-round 2 (N2, nit): memoized too — while `receiptsQuery.data` is
  // undefined (loading/error), the un-memoized `?? []` fallback allocated a
  // fresh empty array every render, which fed the SAME staleness into
  // `heldIds`/`relevantReceipts`/`openReceipts` below that C1 fixed for the
  // settled case. Currently harmless (the accelerator effect's first line
  // bails out via `!receiptsSettled` before any of them are read in that
  // state) — memoizing anyway so a future change to that early-return can't
  // silently reopen C1's failure mode through this exact seam.
  const allReceipts: ApiReceipt[] = useMemo(() => receiptsQuery.data?.items ?? [], [receiptsQuery.data])
  // Fix-round 1 (Critical): `heldIds`/`relevantReceipts`/`openReceipts` used
  // to be plain `.filter()` calls re-evaluated (and re-ALLOCATED) on every
  // render. `openReceipts` sits in the accelerator effect's dependency array
  // below — a fresh array reference every render made that effect re-run on
  // every render too, not just when the receipt data actually changed. The
  // first run correctly checked a unique amount+date match; the very next
  // (state-update-triggered) render re-ran the effect with the one-shot ref
  // already latched, computed `desired = null`, and immediately unchecked the
  // receipt it had just picked — the checkbox visibly ticked and then
  // un-ticked itself, and the accelerator was permanently defeated. `useMemo`
  // gives `openReceipts` (and its siblings, same category of bug even though
  // only `openReceipts` sat in a dependency array) a reference that's stable
  // across renders unless `allReceipts`/`heldIds` themselves actually change
  // — mirrors the original MatchPanel's `receiptsQuery.data?.items ?? []`,
  // which was stable because TanStack Query caches that array by reference.
  const heldIds = useMemo(() => new Set(invoice.receipt_ids ?? []), [invoice.receipt_ids])
  // Candidate pool for this panel: still-open receipts, PLUS whatever this
  // invoice already holds (those are 'reconciled', not 'open' — excluded by
  // an 'open'-only filter, but must still be shown/toggleable here or the
  // operator has no way to see, let alone un-claim, what's already attached).
  const relevantReceipts = useMemo(
    () => allReceipts.filter((r) => r.status === 'open' || heldIds.has(r.id)),
    [allReceipts, heldIds],
  )
  // The genuinely-unclaimed pool — what the two accelerators below are
  // allowed to auto-pick from. A receipt this invoice already holds doesn't
  // need "finding"; it's already checked.
  const openReceipts = useMemo(() => allReceipts.filter((r) => r.status === 'open'), [allReceipts])

  const receiptsSettled = receiptsQuery.isSuccess || receiptsQuery.isError
  // Design decision 3 (task brief): a fetch failure must render as a DISTINCT
  // error state, never silently as "this agreement has no receipts" — that
  // would make the operator think there's genuinely nothing to reconcile and
  // reach for "Settle without receipt evidence", quietly bypassing the
  // evidence chain Tasks 1-7 built.
  const receiptsErrored = receiptsQuery.isError
  const hasReceiptList = relevantReceipts.length > 0
  const sortedReceipts = useMemo(
    () => [...relevantReceipts].sort((a, b) => new Date(b.receipt_date).getTime() - new Date(a.receipt_date).getTime()),
    [relevantReceipts],
  )

  // Say what is actually THERE, not just what is missing. This panel only offers
  // `open` receipts (plus any this invoice already holds), so an agreement whose
  // receipts are every one of them voided / rejected / claimed by some other
  // invoice renders the exact same empty box as an agreement that has none at
  // all — and the copy this replaces named ONE exclusion ("receipts still
  // awaiting AP review aren't listed here") while staying silent about whichever
  // one actually applied. An operator who had just cancelled both receipts on an
  // agreement read "No open receipts on this agreement" and concluded the
  // feature was broken; the sentence had listed the one reason that did NOT
  // apply and omitted the one that did.
  //
  // Costs no extra request: `allReceipts` above is already the UNFILTERED list —
  // the query deliberately sends no status param so this invoice's own claimed
  // (`reconciled`) receipts stay visible and de-selectable — so the breakdown is
  // a count over data already in hand.
  //
  // Statuses are named with statusLabel(), the same table StatusBadge renders
  // from, so the words in this sentence are character-for-character the words on
  // the badges the operator sees on the agreement's own receipt list. Title case
  // is deliberate for that reason: "2 Removed" is meant to point at two badges.
  const emptyReason = useMemo(() => {
    const total = allReceipts.length
    if (total === 0) return 'No receipts have been recorded on this agreement yet.'
    const counts = new Map<ReceiptStatus, number>()
    for (const r of allReceipts) counts.set(r.status, (counts.get(r.status) ?? 0) + 1)
    // Sorted by status VALUE, not by count — a stable order across renders and
    // across agreements, so the same breakdown always reads the same way.
    const breakdown = [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([status, n]) => `${n} ${statusLabel(status)}`)
      .join(', ')
    return (
      `This agreement has ${total} receipt${total === 1 ? '' : 's'} (${breakdown}), ` +
      'but none of them can be attached to this invoice. ' +
      'Only an Open receipt — or one already attached to this invoice — can be selected.'
    )
  }, [allReceipts])

  const [selectedReceiptIds, setSelectedReceiptIds] = useState<string[]>(invoice.receipt_ids ?? [])
  const [varianceReason, setVarianceReason] = useState(invoice.receipt_variance_reason ?? '')
  const [legacyReason, setLegacyReason] = useState(invoice.legacy_settlement_reason ?? '')
  const [receiptRefInput, setReceiptRefInput] = useState('')
  // receiptRefCommitted only updates on blur/Enter, not every keystroke — an
  // in-progress short reference number (e.g. typing "10010") can pass through
  // a DIFFERENT, shorter real receipt number ("1001") on the way to the one
  // actually being typed; matching mid-keystroke would pick that wrong one
  // and never un-pick it.
  const [receiptRefCommitted, setReceiptRefCommitted] = useState('')

  const selectedReceipts = relevantReceipts.filter((r) => selectedReceiptIds.includes(r.id))
  const invoiceTotalAmount = Number(invoice.total_amount)
  const selectedTotal = selectedReceipts.reduce((sum, r) => sum + Number(r.total_amount), 0)
  const varianceAmount = selectedTotal - invoiceTotalAmount
  const varianceIsZero = centsEqual(selectedTotal, invoiceTotalAmount)

  // Tracks which receipt (at most one) is CURRENTLY checked because an
  // accelerator put it there, as opposed to the operator's own click — lets
  // the effect below swap its own pick without ever touching one picked by
  // hand. Ported from MatchPanel's accelerator design (same invariant: at
  // most one accelerator-owned pick at a time, an explicit reference match
  // always wins over the automatic amount+date guess, and a desired pick
  // that's already checked — for ANY reason — is never claimed).
  const autoSelectedReceiptIdRef = useRef<string | null>(null)
  const receiptPreselectAppliedRef = useRef(false)
  const selectedReceiptIdsRef = useRef<string[]>(selectedReceiptIds)
  useEffect(() => {
    selectedReceiptIdsRef.current = selectedReceiptIds
  }, [selectedReceiptIds])

  const toggleReceipt = (receiptId: string) => {
    if (autoSelectedReceiptIdRef.current === receiptId) {
      // The operator is taking manual control of a receipt an accelerator
      // picked — stop treating it as "ours" so a later accelerator swap
      // can't fight a manual (re-)check/uncheck.
      autoSelectedReceiptIdRef.current = null
    }
    setSelectedReceiptIds((prev) => (prev.includes(receiptId) ? prev.filter((id) => id !== receiptId) : [...prev, receiptId]))
  }

  // Combined accelerator decision — ported verbatim (in spirit) from
  // MatchPanel's pre-Task-6 version (git show bcbe02b). Priority: an explicit
  // reference match (path 1) always wins over the automatic amount+date
  // guess (path 2, one-shot). At most one accelerator-picked receipt is ever
  // checked; a desired pick that's already checked — for ANY reason,
  // including the operator's own click — is never claimed, so a manual
  // selection can never be silently unpicked by a later accelerator swap.
  useEffect(() => {
    if (!receiptsSettled) return

    const committedRef = receiptRefCommitted.trim()
    const refMatch = committedRef
      ? openReceipts.find((r) => r.receipt_ref != null && r.receipt_ref.trim() === committedRef) ?? null
      : null

    let desired: ApiReceipt | null = refMatch
    if (!desired && !receiptPreselectAppliedRef.current) {
      const invoiceDateMs = new Date(invoice.invoice_date).getTime()
      const uniqueMatches = openReceipts.filter((r) => {
        if (!centsEqual(Number(r.total_amount), invoiceTotalAmount)) return false
        const daysBefore = (invoiceDateMs - new Date(r.receipt_date).getTime()) / 86_400_000
        return daysBefore >= 0 && daysBefore <= 14
      })
      desired = uniqueMatches.length === 1 ? uniqueMatches[0] : null
      receiptPreselectAppliedRef.current = true
    }

    const desiredId = desired?.id ?? null
    if (desiredId === autoSelectedReceiptIdRef.current) return
    const previousAutoId = autoSelectedReceiptIdRef.current

    if (desiredId !== null && selectedReceiptIdsRef.current.includes(desiredId)) {
      // Already checked, and it isn't our own previous pick (that case
      // returned above) — the operator put it there. Disown it, release
      // only OUR previous pick if we had one, and touch nothing else.
      autoSelectedReceiptIdRef.current = null
      if (previousAutoId) {
        setSelectedReceiptIds((prev) => (prev.includes(previousAutoId) ? prev.filter((id) => id !== previousAutoId) : prev))
      }
      return
    }

    autoSelectedReceiptIdRef.current = desiredId
    setSelectedReceiptIds((prev) => {
      let next = prev
      if (previousAutoId && next.includes(previousAutoId)) next = next.filter((id) => id !== previousAutoId)
      if (desiredId && !next.includes(desiredId)) next = [...next, desiredId]
      return next
    })
  }, [receiptsSettled, openReceipts, receiptRefCommitted, invoiceTotalAmount, invoice.invoice_date])

  const setReceiptsMutation = useSetInvoiceReceipts()
  const settleMutation = useSettleWithoutReceipt()
  const pending = setReceiptsMutation.isPending || settleMutation.isPending

  // Is there anything left to submit? Without this the Save button stayed
  // armed forever and a successful save changed NOTHING on screen — the
  // checkboxes were already ticked before the click, the header above already
  // read "backed by 1 receipt" (it renders from the persisted receipt_ids),
  // and the button came back looking exactly as it did. An operator has no way
  // to tell a save that landed from one that silently didn't, so they click
  // again. And again.
  //
  // `nextVariance` is compared as the mutation would SEND it, not as the
  // textarea holds it: handleSaveReceipts sends null whenever the difference
  // is zero, so a stale reason still sitting in local state (the textarea is
  // unmounted at zero variance, it does not clear itself) is not a change —
  // while a persisted reason that a zero difference would now null out IS one.
  // null and '' are the same value on this comparison, matching the backend:
  // crud/invoice.py stores the reason as NULL, never ''.
  const persistedReceiptIds = invoice.receipt_ids ?? []
  const nextVariance = varianceIsZero ? '' : varianceReason.trim()
  const receiptsDirty =
    persistedReceiptIds.length !== selectedReceiptIds.length ||
    !selectedReceiptIds.every((id) => heldIds.has(id)) ||
    nextVariance !== (invoice.receipt_variance_reason ?? '')

  // Fix-round 2 (N1): a successful save/settle must forget the accelerator's
  // ownership of whatever it had picked, SYNCHRONOUSLY with the mutation
  // succeeding — not by waiting for the next accelerator effect run to work
  // it out from heldIds/openReceipts. Why that matters: this mutation's
  // onSuccess (useSetInvoiceReceipts) invalidates BOTH `['invoices', id]`
  // (which is what updates `invoice.receipt_ids`/heldIds) and the receipts
  // list query (which is what drops the just-claimed receipt out of
  // `openReceipts`) — two independent queries, refetched and committed to
  // React state independently. There is no guarantee both land in the same
  // render: if `openReceipts` updates (receipt no longer 'open') before
  // `heldIds` catches up (still doesn't list it as held yet), the
  // accelerator effect runs with a stale `heldIds` and un-checks a receipt
  // the backend just confirmed — the exact bug this whole fix round exists
  // to close, just reached through a timing gap instead of C1's unconditional
  // one. Clearing the ref the instant `mutate()` resolves sidesteps the race
  // entirely: on the next effect run, `desiredId` (null — no ref text, the
  // one-shot amount+date guess already spent) trivially equals
  // `autoSelectedReceiptIdRef.current` (also null), so the effect returns on
  // its very first line and never touches `selectedReceiptIds` at all.
  const handleSaveReceipts = () => {
    setReceiptsMutation.mutate({
      id: invoice.id,
      receipt_ids: selectedReceiptIds,
      variance_reason: varianceIsZero ? null : varianceReason.trim(),
    }, {
      onSuccess: () => { autoSelectedReceiptIdRef.current = null },
    })
  }

  const handleSettleWithoutReceipt = () => {
    if (!legacyReason.trim()) return
    settleMutation.mutate({ id: invoice.id, reason: legacyReason.trim() }, {
      onSuccess: () => { autoSelectedReceiptIdRef.current = null },
    })
  }

  const activeError = setReceiptsMutation.error ?? settleMutation.error

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-4 flex flex-col gap-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-neutral-400">Receipt Evidence</p>

      {/* Reference-number accelerator — matches on blur/Enter, never on every
          keystroke (see the comment above receiptRefCommitted). */}
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-700">
          Reference on the invoice (optional)
        </label>
        <input
          type="text"
          value={receiptRefInput}
          onChange={(e) => setReceiptRefInput(e.target.value)}
          onBlur={() => setReceiptRefCommitted(receiptRefInput)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return
            e.preventDefault()
            setReceiptRefCommitted(receiptRefInput)
          }}
          placeholder="e.g. the receipt or delivery note # printed on the invoice"
          className="h-8 px-3 rounded-lg border border-neutral-300 bg-white text-xs focus:outline-none focus:ring-1 focus:ring-primary-600"
        />
      </div>

      {/* A fetch failure is NEVER rendered as an empty receipt list — that
          would read as "this agreement genuinely has no receipts" and send
          the operator straight to the no-evidence settlement action below,
          silently bypassing the evidence chain. */}
      {receiptsErrored && !hasReceiptList && (
        <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <div className="flex flex-1 items-center justify-between gap-2">
            <p>Couldn't load this agreement's receipts — the list below may be incomplete. Do not settle without receipt evidence until this loads.</p>
            <Button size="sm" variant="secondary" onClick={() => receiptsQuery.refetch()} disabled={receiptsQuery.isFetching}>
              {receiptsQuery.isFetching ? 'Retrying…' : 'Retry'}
            </Button>
          </div>
        </div>
      )}

      {(!receiptsErrored || hasReceiptList) && (
        <>
          {receiptsErrored && hasReceiptList && (
            <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <div className="flex flex-1 items-center justify-between gap-2">
                <p>Couldn't refresh the receipt list — showing the last one loaded.</p>
                <Button size="sm" variant="secondary" onClick={() => receiptsQuery.refetch()} disabled={receiptsQuery.isFetching}>
                  {receiptsQuery.isFetching ? 'Retrying…' : 'Retry'}
                </Button>
              </div>
            </div>
          )}
          {!receiptsSettled ? (
            <p className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-4 text-center text-xs text-neutral-400">
              Loading receipts…
            </p>
          ) : sortedReceipts.length === 0 ? (
            <p className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-4 text-center text-xs text-neutral-400">
              {emptyReason}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-neutral-700">
                Which receipt(s) does this invoice cover?
              </label>
              <div className="flex flex-col gap-2">
                {sortedReceipts.map((receipt) => (
                  <ReceiptCandidateRow
                    key={receipt.id}
                    receipt={receipt}
                    selected={selectedReceiptIds.includes(receipt.id)}
                    onToggle={() => toggleReceipt(receipt.id)}
                    currency={currency}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {selectedReceiptIds.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2.5 text-xs">
            <span className="text-neutral-500">
              Selected {formatAmount(selectedTotal, currency)}
              {' · '}Invoice {formatAmount(invoiceTotalAmount, currency)}
            </span>
            <span className={cn('font-medium', varianceIsZero ? 'text-success-700' : 'text-warning-700')}>
              Difference {formatAmount(varianceAmount, currency)}
            </span>
          </div>
          {/* A non-zero difference asks for an explanation but never blocks
              submit — counter purchases routinely differ from the receipt
              total by freight, discounts, or tax. */}
          {!varianceIsZero && (
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700">
                Explain the difference <span className="text-warning-700">(recommended)</span>
              </label>
              <textarea
                rows={2}
                value={varianceReason}
                onChange={(e) => setVarianceReason(e.target.value)}
                placeholder="e.g. Invoice includes freight not itemized on the receipt."
                className="px-3 py-2 rounded-lg border border-neutral-300 bg-white text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary-600"
              />
            </div>
          )}
          {/* The save state is stated in words, not left to be inferred from a
              button that looks identical either way. `isSuccess` distinguishes
              "you just saved this" from "this was already the stored state when
              the page opened" — both are clean, but only one of them answers
              the click that was just made. */}
          <div className="flex items-center justify-end gap-3">
            {!receiptsDirty && !pending && (
              <span className="flex items-center gap-1.5 text-xs text-success-700">
                <CheckCircle2 className="h-3.5 w-3.5" />
                {setReceiptsMutation.isSuccess ? 'Receipt evidence saved' : 'Saved — no changes to submit'}
              </span>
            )}
            <Button size="sm" onClick={handleSaveReceipts} disabled={pending || !receiptsDirty}>
              {setReceiptsMutation.isPending ? 'Saving…' : 'Save Receipt Evidence'}
            </Button>
          </div>
        </div>
      )}

      {/* Fix-round 1 (Important 2): unchecking every receipt used to leave
          "Settle without receipt evidence" as the ONLY way forward — an
          operator who just unclaimed a wrongly-attached receipt (meant for a
          different invoice) had no way back to a clean, undeclared state
          without either re-checking something wrong or permanently stamping
          legacy_settlement=true. PUT /receipts with an empty receipt_ids
          array is a legal, ordinary call (crud/invoice.py set_receipts) that
          releases everything and touches legacy_settlement not at all — this
          reuses the exact same handler as "Save Receipt Evidence" above,
          just with an empty selection. Only offered when there's actually
          something held to detach; a never-attached invoice has nothing to
          detach and goes straight to the settle-without-evidence path below. */}
      {selectedReceiptIds.length === 0 && receiptsSettled && (invoice.receipt_ids?.length ?? 0) > 0 && (
        <div className="flex justify-end">
          <Button size="sm" variant="secondary" onClick={handleSaveReceipts} disabled={pending || !receiptsDirty}>
            {setReceiptsMutation.isPending ? 'Saving…' : 'Detach All Receipts'}
          </Button>
        </div>
      )}

      {selectedReceiptIds.length === 0 && receiptsSettled && (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">
            No receipts selected — reason for settling without receipt evidence <span className="text-danger-600">*</span>
          </label>
          <p className="text-[11px] text-neutral-500">
            This invoice will be paid against the agreement with no receipt to reconcile against.
            Explain why — this is recorded for audit.
          </p>
          {receiptsErrored && (
            <p className="text-[11px] font-medium text-warning-700">
              The receipt list failed to load — settling now will record this invoice as having no receipt evidence, even if receipts actually exist. Consider retrying above first.
            </p>
          )}
          <textarea
            rows={3}
            value={legacyReason}
            onChange={(e) => setLegacyReason(e.target.value)}
            placeholder="e.g. Monthly house-account statement; the receipts it covers have not been digitized."
            className="px-3 py-2 rounded-lg border border-neutral-300 bg-white text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary-600"
          />
          <div className="flex justify-end">
            <Button size="sm" onClick={handleSettleWithoutReceipt} disabled={pending || !legacyReason.trim()}>
              {settleMutation.isPending ? 'Saving…' : 'Settle Without Receipt Evidence'}
            </Button>
          </div>
        </div>
      )}

      {/* Both write endpoints 422 when a non-cancelled PA still references
          this invoice (crud/invoice.py _invoice_referenced_by_active_pa) —
          the detail message names that PA's number and status, and is the
          operator's ONLY way to learn which PA is blocking them. api.ts's
          request() throws Error(detail) with that exact string as the
          message, so surfacing err.message here reproduces it verbatim. */}
      {activeError && (
        <div className="flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {activeError instanceof Error ? activeError.message : 'Could not update receipt evidence'}
        </div>
      )}
    </div>
  )
}

// Task 8: reconcile receipt evidence against an invoice from its own detail
// page — the counterpart to the candidate-picking UI Task 6 removed from
// MatchPanel (matching an agreement is now pure linkage; attaching evidence
// is this separate, later action). Only ever renders for a house_account
// agreement — recurring/milestone settle from schedule rows and have no
// receipts at all.
//
// Fix-round 1 (Important 1): this used to determine agreement_type by
// fetching /invoices/{id}/agreement-candidates, gated on
// _require_invoice_match_access (AP roles ∪ uploader ∪ the holder of an open
// match_invoice task). Every OTHER role — warehouse_staff, supervisor, cfo,
// vendor_manager, erp_pa_officer among them — got `agreement === undefined`
// there, which this component happened to handle safely (render nothing),
// but the SAME undefined value fed InvoiceDetailPage.tsx's evidence-summary
// copy, which had no such guard: it fell back to claiming a zero-evidence
// house_account invoice was "settled against the agreement itself" — the
// exact Phase-1A lie this feature exists to retire — for every one of those
// roles. Reading `invoice.agreement_type` (a denormalized snapshot written
// at match time, see ag05_invoice_agreement_type / InvoiceResponse) fixes
// both call sites from one additive field: it's on the SAME response every
// caller who can read this invoice already receives, no separate request,
// no separate permission gate, and no "determination pending/failed" state
// to fall back from at all.
export function InvoiceReceiptsPanel({ invoice }: { invoice: ApiInvoice }) {
  if (!invoice.agreement_id || invoice.agreement_type !== 'house_account') return null

  return <InvoiceReceiptsPanelBody invoice={invoice} agreementId={invoice.agreement_id} />
}
