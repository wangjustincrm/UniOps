import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { useDeclineMatch, useMatchCandidates, useMatchInvoice } from '@/hooks/useInvoices'
import { useAgreementCandidates, useAgreementSchedule } from '@/hooks/useAgreements'
import { useAgreementSlips } from '@/hooks/useAgreementSlips'
import { useAuthStore } from '@/stores/auth.store'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import type { ApiInvoice, AllocationInput, NonPoLineInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'
import type { ApiAgreement, ApiScheduleRow } from '@/services/agreement'
import type { ApiSlip } from '@/services/agreementSlips'
import type { DocumentStatus } from '@/types'
import { InvoiceAllocationPanel } from './InvoiceAllocationPanel'

type Route = 'po' | 'agreement'

// One selectable agreement candidate row — number, title, validity window,
// and Consumed/NTE (mirrors AgreementListPage's ConsumedCell; decimals arrive
// as JSON strings so every arithmetic site here goes through Number()).
function AgreementCandidateRow({
  agreement, selected, onSelect,
}: { agreement: ApiAgreement; selected: boolean; onSelect: () => void }) {
  const consumed = Number(agreement.consumed_amount)
  const ceiling = agreement.not_to_exceed ? Number(agreement.not_to_exceed) : null
  const overCeiling = ceiling !== null && consumed > ceiling

  return (
    <label
      className={cn(
        'flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors',
        selected ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white hover:bg-neutral-50',
      )}
    >
      <input
        type="radio"
        name="agreement-candidate"
        checked={selected}
        onChange={onSelect}
        className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 font-mono text-sm font-medium text-neutral-900">
            {agreement.number}
            <StatusBadge status={agreement.status as DocumentStatus} />
          </span>
          <span className={cn('shrink-0 text-xs font-medium', overCeiling ? 'text-danger-600' : 'text-neutral-500')}>
            Consumed {formatAmount(consumed, agreement.currency)}
            {ceiling !== null ? ` / NTE ${formatAmount(ceiling, agreement.currency)}` : ' / No ceiling'}
          </span>
        </div>
        <p className="truncate text-sm text-neutral-700">{agreement.title}</p>
        <p className="text-[11px] text-neutral-400">
          Valid {formatDate(agreement.valid_from)} – {formatDate(agreement.valid_to)}
          {agreement.status === 'expired' && ' · expired, still within its grace window'}
        </p>
      </div>
    </label>
  )
}

// Mirrors the backend's claim_next_period tolerance check (crud/agreement_schedule.py):
// expected ± (expected * tolerance_pct / 100). Both operands must already be
// Number()-coerced by the caller — decimal fields arrive as JSON strings.
function withinPeriodTolerance(amount: number, expected: number, tolerancePct: number): boolean {
  const span = (expected * tolerancePct) / 100
  return amount >= expected - span && amount <= expected + span
}

// Cent-rounded equality — plain float subtraction of two Number()-coerced
// decimal strings can land a hair off zero (e.g. summing several selected
// slips' totals), which would otherwise make a genuinely-even match look
// like it has a variance and wrongly nag for a slip_variance_reason, or
// (worse) silently attach a spurious slip_variance_reason to a payload that
// should have omitted it entirely.
function centsEqual(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100)
}

// recurring — preview of the period the server will FIFO-claim on submit (no
// date-window guessing on either side, see claim_next_period's comment).
function RecurringPeriodPreview({
  loading, error, row, invoiceTotal, currency,
}: { loading: boolean; error: boolean; row: ApiScheduleRow | undefined; invoiceTotal: number; currency: string }) {
  if (loading) {
    return (
      <p className="rounded-lg border border-neutral-200 bg-white px-3 py-2.5 text-xs text-neutral-400">
        Loading the payment schedule…
      </p>
    )
  }
  // Whole-branch review finding: GET /agreements/{id}/schedule needs
  // epms.agreement.read (12 roles), but matching itself is authorised by the
  // invoice's own match permission — a delegate outside those 12 roles gets
  // a 403 here, which used to render as "No open period is available" (the
  // SAME copy as the genuinely-empty case) even though periods may well
  // exist. Told apart so the operator knows it's a permission gap, not an
  // empty schedule — and submit is never disabled by it (see
  // canSubmitAgreement): the server still FIFO-claims or routes to review
  // exactly as if nothing had been previewed at all.
  if (error) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <p>Couldn't load the payment schedule — you may not have permission to view it. You can still submit; the server will assign a period or send this to review.</p>
      </div>
    )
  }
  if (!row) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <p>No open period is available to claim on this agreement — this invoice will go to review for manual assignment, unless you assign one below.</p>
      </div>
    )
  }
  const label = row.period_label ?? `Period #${row.sequence}`
  const expected = row.expected_amount != null ? Number(row.expected_amount) : null
  const tolerancePct = row.tolerance_pct != null ? Number(row.tolerance_pct) : 0
  const outOfTolerance = expected !== null && !withinPeriodTolerance(invoiceTotal, expected, tolerancePct)

  return (
    <div className="flex flex-col gap-1.5">
      <div className="rounded-lg border border-primary-200 bg-white px-3 py-2.5 text-xs text-neutral-700">
        This invoice will be claimed against <span className="font-medium text-neutral-900">{label}</span>
        {expected !== null && <> (expected {formatAmount(expected, currency)})</>}
        {row.status === 'overdue' && <span className="ml-1.5 font-medium text-warning-700">· overdue</span>}
      </div>
      {outOfTolerance && (
        <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <p>Amount is outside the tolerance for {label} — this invoice will go to review for manual assignment, unless you assign a period below.</p>
        </div>
      )}
    </div>
  )
}

// recurring override — the manual-assignment escape hatch (whole-branch
// review Blocker 2, spec §4.3 step 5): when FIFO can't place the invoice
// (out of tolerance / nothing claimable) or the schedule couldn't even be
// read, offer the same style of picker the milestone branch uses so a human
// can explicitly assign a period, skipping the tolerance check on the
// server. Same interaction shape as MilestoneStageRow, but this one is
// OPTIONAL — submitting with nothing picked here still works exactly as
// before (server FIFO-claims, or routes to review).
function PeriodOverrideRow({
  row, selected, onSelect, currency,
}: { row: ApiScheduleRow; selected: boolean; onSelect: () => void; currency: string }) {
  const expected = row.expected_amount != null ? Number(row.expected_amount) : null
  return (
    <label
      className={cn(
        'flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors',
        selected ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white hover:bg-neutral-50',
      )}
    >
      <input
        type="radio"
        name="period-override"
        checked={selected}
        onChange={onSelect}
        className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-neutral-900">
          {row.period_label ?? `Period #${row.sequence}`}
          {row.status === 'overdue' && <span className="ml-1.5 font-medium text-warning-700">· overdue</span>}
        </p>
        <p className="text-xs text-neutral-500">
          {row.expected_date ? `Expected ${formatDate(row.expected_date)}` : 'No expected date'}
          {expected !== null && ` · expected ${formatAmount(expected, currency)}`}
        </p>
      </div>
    </label>
  )
}

// milestone — single-select stage row. Only rows with invoice_id === null are
// ever passed in (a claimed stage must not be offered again).
function MilestoneStageRow({
  row, selected, onSelect, currency,
}: { row: ApiScheduleRow; selected: boolean; onSelect: () => void; currency: string }) {
  const expected = row.expected_amount != null ? Number(row.expected_amount) : null
  return (
    <label
      className={cn(
        'flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors',
        selected ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white hover:bg-neutral-50',
      )}
    >
      <input
        type="radio"
        name="milestone-stage"
        checked={selected}
        onChange={onSelect}
        className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-neutral-900">{row.milestone_name ?? `Stage #${row.sequence}`}</p>
        <p className="text-xs text-neutral-500">
          {row.expected_timing ?? 'No timing specified'}
          {expected !== null && ` · Expected ${formatAmount(expected, currency)}`}
        </p>
      </div>
    </label>
  )
}

// house_account — multi-select pickup slip row (Task 10). Only `open` slips
// are ever passed in (see the slipsQuery below) — a 'reconciled' slip is
// already claimed by some other invoice, 'pending_ap_review' isn't
// AP-cleared yet, 'voided'/'rejected' are dead.
function SlipCandidateRow({
  slip, selected, onToggle, currency,
}: { slip: ApiSlip; selected: boolean; onToggle: () => void; currency: string }) {
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
          <span className="text-sm font-medium text-neutral-900">
            {slip.slip_ref ?? 'No reference #'}
          </span>
          <span className="shrink-0 text-xs font-medium text-neutral-700">
            {formatAmount(Number(slip.total_amount), currency)}
          </span>
        </div>
        <p className="text-[11px] text-neutral-400">Picked up {formatDate(slip.slip_date)}</p>
      </div>
    </label>
  )
}

// Inline 3-way match panel — used by the Unmatched queue rows AND the invoice
// detail page (Task Inbox / email deep links land there).
export function MatchPanel({ inv, onClose }: { inv: ApiInvoice; onClose: () => void }) {
  // Candidates come from the invoice-scoped endpoint (same vendor, open POs,
  // authorized by match rights) — the general PO list is access-scoped and can
  // be EMPTY for task assignees without related PRs.
  const { data: candData, isLoading: candLoading } = useMatchCandidates(inv.id)
  // Agreement candidates: same vendor, inside the admission window — already
  // filtered server-side (active, or expired-but-within-grace). Do not filter
  // further here; the server's rule is the rule.
  const { data: agrCandData, isLoading: agrCandLoading } = useAgreementCandidates(inv.id)
  const matchInvoiceMutation = useMatchInvoice()
  const declineMutation = useDeclineMatch()
  const navigate = useNavigate()

  const [poSearch, setPoSearch] = useState('')
  const [declining, setDeclining] = useState(false)
  const [declineNote, setDeclineNote] = useState('')

  // Phase 1A does no auto-resolution — the operator always picks the tab.
  // Default to 'po' UNLESS there are zero PO candidates and at least one
  // agreement candidate: silently preferring agreements would mis-route
  // ordinary PO invoices. Applied once, after both candidate lists have
  // loaded, so it never overrides a manual tab switch.
  const [route, setRoute] = useState<Route>('po')
  const defaultAppliedRef = useRef(false)
  const rawPoCandidates = candData?.items ?? []
  const agreementCandidates: ApiAgreement[] = agrCandData?.items ?? []
  useEffect(() => {
    if (defaultAppliedRef.current || candLoading || agrCandLoading) return
    defaultAppliedRef.current = true
    if (rawPoCandidates.length === 0 && agreementCandidates.length > 0) setRoute('agreement')
  }, [candLoading, agrCandLoading, rawPoCandidates.length, agreementCandidates.length])

  const [selectedAgreementId, setSelectedAgreementId] = useState('')
  const [legacyReason, setLegacyReason] = useState('')
  const [selectedScheduleId, setSelectedScheduleId] = useState('')
  // house_account slip selection (Task 10) — see the slipsQuery block below.
  const [selectedSlipIds, setSelectedSlipIds] = useState<string[]>([])
  const [slipVarianceReason, setSlipVarianceReason] = useState('')
  const [slipRefInput, setSlipRefInput] = useState('')

  const selectedAgreement = agreementCandidates.find((a) => a.id === selectedAgreementId)
  const isHouseAccount = selectedAgreement?.agreement_type === 'house_account'
  const isRecurring = selectedAgreement?.agreement_type === 'recurring'
  const isMilestone = selectedAgreement?.agreement_type === 'milestone'

  // house_account — candidate pickup slips this invoice might be settling
  // (Task 10; real evidence built in Tasks 1-9). Only 'open' slips are
  // eligible: 'reconciled' is already claimed by another invoice,
  // 'pending_ap_review' isn't AP-cleared yet, 'voided'/'rejected' are dead.
  // Gated the same way scheduleQuery is above — enabled only when needed.
  const slipsQuery = useAgreementSlips(isHouseAccount ? selectedAgreementId : '', 'open')
  const openSlips: ApiSlip[] = slipsQuery.data?.items ?? []
  const slipsLoading = slipsQuery.isLoading
  // Design decision 3 (task brief): a fetch failure must render as a DISTINCT
  // error state, never silently as "this agreement has no slips" — that
  // would make the operator think the agreement is genuinely clean and send
  // them to the legacy no-evidence reason box, quietly bypassing the
  // evidence chain Tasks 1-9 built. See the error branch rendered below.
  const slipsErrored = slipsQuery.isError
  const sortedOpenSlips = [...openSlips].sort(
    (a, b) => new Date(b.slip_date).getTime() - new Date(a.slip_date).getTime()
  )
  const selectedSlips = openSlips.filter((s) => selectedSlipIds.includes(s.id))
  const invoiceTotalAmount = Number(inv.total_amount)
  const selectedSlipTotal = selectedSlips.reduce((sum, s) => sum + Number(s.total_amount), 0)
  const slipVarianceAmount = selectedSlipTotal - invoiceTotalAmount
  const slipVarianceIsZero = centsEqual(selectedSlipTotal, invoiceTotalAmount)

  const toggleSlip = (slipId: string) => {
    setSelectedSlipIds((prev) => (prev.includes(slipId) ? prev.filter((id) => id !== slipId) : [...prev, slipId]))
  }

  // Accelerated path 1 (task brief): typing the invoice's own reference
  // number pre-selects the matching slip, if one exists. Reactive to typing
  // (not a one-shot effect) so it fires the moment a match appears, per the
  // brief's manual-verification script (#6). Deliberately does not
  // deselect anything when the input is cleared or changed — the operator
  // stays in control of what's checked (design decision 1).
  useEffect(() => {
    if (!isHouseAccount) return
    const ref = slipRefInput.trim()
    if (!ref) return
    const match = openSlips.find((s) => s.slip_ref != null && s.slip_ref.trim() === ref)
    if (match) setSelectedSlipIds((prev) => (prev.includes(match.id) ? prev : [...prev, match.id]))
  }, [isHouseAccount, slipRefInput, openSlips])

  // Accelerated path 2 (task brief, design decision 1): pre-select ONLY when
  // exactly one open slip has a total_amount equal to the invoice total AND
  // a slip_date within the 14 days before the invoice date. Multiple matches
  // = do NOT pre-select any of them — a wrong pre-selection the operator
  // waves through is worse than none at all. Runs once per agreement
  // selection (guarded by slipPreselectAppliedRef, reset alongside the other
  // per-agreement state in AgreementCandidateRow's onSelect below) so it
  // never overrides a manual (de)selection made after slips finish loading.
  const slipPreselectAppliedRef = useRef(false)
  useEffect(() => {
    if (!isHouseAccount || slipPreselectAppliedRef.current || slipsLoading) return
    slipPreselectAppliedRef.current = true
    const invoiceDateMs = new Date(inv.invoice_date).getTime()
    const uniqueMatches = openSlips.filter((s) => {
      if (!centsEqual(Number(s.total_amount), invoiceTotalAmount)) return false
      const daysBefore = (invoiceDateMs - new Date(s.slip_date).getTime()) / 86_400_000
      return daysBefore >= 0 && daysBefore <= 14
    })
    if (uniqueMatches.length === 1) {
      setSelectedSlipIds((prev) => (prev.includes(uniqueMatches[0].id) ? prev : [...prev, uniqueMatches[0].id]))
    }
  }, [isHouseAccount, slipsLoading, openSlips, invoiceTotalAmount, inv.invoice_date])

  // Task 6's backend branches on agreement_type: recurring FIFO-claims a period
  // by default (schedule_id optional — see the manual-assignment override
  // below), milestone requires a manually-picked schedule_id, house_account
  // has no schedule rows at all. Only fetch when the selection actually needs
  // it — the hook itself gates on a truthy id.
  const scheduleQuery = useAgreementSchedule(isRecurring || isMilestone ? selectedAgreementId : '')
  const scheduleRows: ApiScheduleRow[] = scheduleQuery.data?.items ?? []
  const scheduleLoading = scheduleQuery.isLoading
  // Whole-branch review finding (Item 8): a permission 403 on the schedule
  // fetch settles isLoading to false with no data — indistinguishable from
  // "genuinely nothing scheduled" unless told apart explicitly. See
  // RecurringPeriodPreview's `error` branch and the milestone section below.
  const scheduleErrored = scheduleQuery.isError

  const unclaimedPeriodRows = isRecurring
    ? [...scheduleRows]
        .filter((r) => r.schedule_type === 'period' && (r.status === 'pending' || r.status === 'overdue'))
        .sort((a, b) => a.sequence - b.sequence)
    : []
  const nextPeriodRow = unclaimedPeriodRows[0]
  const nextPeriodExpected = nextPeriodRow?.expected_amount != null ? Number(nextPeriodRow.expected_amount) : null
  const nextPeriodTolerancePct = nextPeriodRow?.tolerance_pct != null ? Number(nextPeriodRow.tolerance_pct) : 0
  const nextPeriodOutOfTolerance = !!nextPeriodRow && nextPeriodExpected !== null &&
    !withinPeriodTolerance(Number(inv.total_amount), nextPeriodExpected, nextPeriodTolerancePct)
  // Whole-branch review Blocker 2: offer the manual-assignment picker whenever
  // the automatic FIFO claim can't be trusted — nothing claimable, the
  // candidate is out of tolerance, or the schedule couldn't even be read. This
  // is an OVERRIDE, not a requirement (see canSubmitAgreement below) — the
  // spec's escape hatch is for the operator to reach for when they need it,
  // not a mandatory extra step on every recurring match.
  const showPeriodOverride = isRecurring && !scheduleLoading &&
    (scheduleErrored || !nextPeriodRow || nextPeriodOutOfTolerance)

  const milestoneRows = isMilestone
    ? [...scheduleRows]
        .filter((r) => r.schedule_type === 'milestone' && r.invoice_id === null)
        .sort((a, b) => a.sequence - b.sequence)
    : []

  const canSubmitAgreement = !!selectedAgreementId && (
    // house_account (Task 10): real evidence takes priority — any slip(s)
    // selected is sufficient to submit regardless of variance (design
    // decision 2: a non-zero variance asks for an explanation but never
    // blocks submit). Only when NOTHING is selected does the legacy
    // no-evidence reason become the (still mandatory) gate — same rule the
    // backend enforces in _match_to_agreement.
    isHouseAccount ? (selectedSlipIds.length > 0 || legacyReason.trim().length > 0) :
    // A schedule-fetch error must never disable submit (whole-branch review
    // Item 8) — a delegate who can match but can't read the schedule would
    // otherwise be stuck with no path forward. Falling through to the
    // backend's own "Pick the milestone stage" error is more honest than a
    // permanently-disabled button with a misleading "no unclaimed stages".
    isMilestone ? (!scheduleLoading && (!!selectedScheduleId || scheduleErrored)) :
    isRecurring ? !scheduleLoading :
    false
  )

  const me = useAuthStore.getState().user
  const isMyAssignment = inv.match_assignee_id != null && inv.match_assignee_id === me?.id

  const matchablePOs: ApiPo[] = rawPoCandidates.filter((p) =>
    poSearch === '' ||
    p.number.toLowerCase().includes(poSearch.toLowerCase()) ||
    p.vendor_name.toLowerCase().includes(poSearch.toLowerCase())
  )

  const handleMatch = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => {
    matchInvoiceMutation.mutate(
      { id: inv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines,
        reference_po_id: payload.referencePoId ?? undefined },
      {
        onSuccess: () => {
          onClose()
          navigate(`/invoices/${inv.id}`)
        },
      }
    )
  }

  const handleMatchAgreement = () => {
    if (!canSubmitAgreement) return
    // house_account (Task 10): real evidence (slip_ids) takes priority over
    // the no-evidence fallback, mirroring _match_to_agreement's own branch
    // (epms-api/app/crud/invoice.py) field-for-field — slip_ids given =>
    // legacy_settlement_reason is never sent (the backend would ignore it
    // anyway once slip_ids is non-empty, but omitting it keeps the payload
    // honest about which path was taken). slip_variance_reason rides along
    // only when the claimed slips don't net to the invoice total (design
    // decision 2 — it explains a variance, it never blocks submission).
    // recurring: schedule_id is OPTIONAL — omitted, the server FIFO-claims
    // the next pending/overdue period itself; set, it's the
    // manual-assignment override (whole-branch review Blocker 2) and skips
    // the tolerance check entirely. milestone: schedule_id required, no
    // reason (Task 6 branch — see MatchPanel brief). Sent only when truthy
    // in both branches — an empty string would parse as an invalid UUID
    // server-side and surface pydantic's raw 422 instead of the friendlier
    // "Pick the milestone stage" AgreementMatchInvalid message (reachable
    // now that isMilestone's canSubmit can pass with nothing picked, on a
    // schedule-fetch error).
    matchInvoiceMutation.mutate(
      {
        id: inv.id,
        agreement_id: selectedAgreementId,
        ...(isHouseAccount
          ? (selectedSlipIds.length > 0
              ? { slip_ids: selectedSlipIds,
                  ...(!slipVarianceIsZero ? { slip_variance_reason: slipVarianceReason.trim() } : {}) }
              : { legacy_settlement_reason: legacyReason.trim() })
          : {}),
        ...((isMilestone || isRecurring) && selectedScheduleId ? { schedule_id: selectedScheduleId } : {}),
      },
      {
        onSuccess: () => {
          onClose()
          navigate(`/invoices/${inv.id}`)
        },
      }
    )
  }

  const handleDecline = () => {
    if (!declineNote.trim()) return
    declineMutation.mutate(
      { id: inv.id, note: declineNote.trim() },
      { onSuccess: onClose }
    )
  }

  return (
    <div className="mt-2 rounded-xl border border-primary-200 bg-primary-50 p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-primary-700">Match Invoice</p>
        <button onClick={onClose} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Route switch — Phase 1A does no auto-resolution, the operator always
          picks the tab. See the default-tab effect above for the one case
          ('po' has zero candidates) where 'agreement' is pre-selected. */}
      <div className="flex gap-4 border-b border-primary-200/60">
        <button
          type="button"
          onClick={() => setRoute('po')}
          className={cn(
            'px-1 pb-2 text-xs font-medium border-b-2 -mb-px transition-colors',
            route === 'po' ? 'border-primary-600 text-primary-700' : 'border-transparent text-neutral-500 hover:text-neutral-700',
          )}
        >
          Purchase Orders ({rawPoCandidates.length})
        </button>
        <button
          type="button"
          onClick={() => setRoute('agreement')}
          className={cn(
            'px-1 pb-2 text-xs font-medium border-b-2 -mb-px transition-colors',
            route === 'agreement' ? 'border-primary-600 text-primary-700' : 'border-transparent text-neutral-500 hover:text-neutral-700',
          )}
        >
          Agreements ({agreementCandidates.length})
        </button>
      </div>

      {route === 'po' ? (
        <>
          {/* PO search — narrows the candidate POs shown as drop targets */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-neutral-400 pointer-events-none" />
            <input
              type="text"
              placeholder="Filter candidate POs by number or vendor..."
              value={poSearch}
              onChange={(e) => setPoSearch(e.target.value)}
              className="w-full h-8 pl-7 pr-3 rounded-lg border border-neutral-300 bg-white text-xs focus:outline-none focus:ring-1 focus:ring-primary-600"
            />
          </div>

          {candLoading ? (
            <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
              Loading candidate purchase orders…
            </p>
          ) : matchablePOs.length === 0 ? (
            <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
              No open purchase orders found for this vendor.
            </p>
          ) : (
            <InvoiceAllocationPanel
              invoice={inv}
              pos={matchablePOs}
              submitting={matchInvoiceMutation.isPending}
              onSubmit={handleMatch}
            />
          )}
        </>
      ) : (
        <div className="flex flex-col gap-3">
          {agrCandLoading ? (
            <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
              Loading candidate agreements…
            </p>
          ) : agreementCandidates.length === 0 ? (
            <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
              No agreements found for this vendor within their admission window.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {agreementCandidates.map((agr) => (
                <AgreementCandidateRow
                  key={agr.id}
                  agreement={agr}
                  selected={selectedAgreementId === agr.id}
                  onSelect={() => {
                    // A previously-picked stage/slip selection belongs to the
                    // PREVIOUS agreement — stale if left set across a
                    // selection change. Also re-arms the accelerated-path
                    // pre-selection (slipPreselectAppliedRef) so it runs
                    // fresh against the NEW agreement's slips.
                    setSelectedAgreementId(agr.id)
                    setSelectedScheduleId('')
                    setSelectedSlipIds([])
                    setSlipVarianceReason('')
                    setSlipRefInput('')
                    setLegacyReason('')
                    slipPreselectAppliedRef.current = false
                  }}
                />
              ))}
            </div>
          )}

          {/* house_account — pick the pickup slip(s) this invoice covers
              (Task 10; real evidence built in Tasks 1-9). Falls back to the
              1A no-evidence reason box ONLY when nothing is selected — see
              design note above canSubmitAgreement. */}
          {isHouseAccount && (
            <div className="flex flex-col gap-3">
              {/* Reference-number accelerator (design decision 1, path 1):
                  matching is exact against slip_ref — no fuzzy search. Does
                  not deselect anything on its own; the operator stays in
                  control. */}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700">
                  Reference on the invoice (optional)
                </label>
                <input
                  type="text"
                  value={slipRefInput}
                  onChange={(e) => setSlipRefInput(e.target.value)}
                  placeholder="e.g. the counter receipt # printed on the invoice"
                  className="h-8 px-3 rounded-lg border border-neutral-300 bg-white text-xs focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </div>

              {slipsErrored ? (
                // Design decision 3: a fetch failure is NEVER rendered as an
                // empty slip list — that would read as "this agreement
                // genuinely has no slips" and send the operator straight to
                // the no-evidence reason box below, silently bypassing the
                // evidence chain. Submit stays enabled either way (via the
                // no-evidence fallback that appears since selectedSlipIds
                // stays empty here).
                <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <p>Couldn't load pickup slips — you may not have permission to view them. You can still submit by explaining why below.</p>
                </div>
              ) : slipsLoading ? (
                <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
                  Loading pickup slips…
                </p>
              ) : sortedOpenSlips.length === 0 ? (
                <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
                  No open pickup slips on this agreement.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  <label className="text-xs font-medium text-neutral-700">
                    Which pickup slip(s) does this invoice cover?
                  </label>
                  <div className="flex flex-col gap-2">
                    {sortedOpenSlips.map((slip) => (
                      <SlipCandidateRow
                        key={slip.id}
                        slip={slip}
                        selected={selectedSlipIds.includes(slip.id)}
                        onToggle={() => toggleSlip(slip.id)}
                        currency={selectedAgreement?.currency ?? inv.currency}
                      />
                    ))}
                  </div>
                </div>
              )}

              {selectedSlipIds.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-3 py-2.5 text-xs">
                    <span className="text-neutral-500">
                      Selected {formatAmount(selectedSlipTotal, selectedAgreement?.currency ?? inv.currency)}
                      {' · '}Invoice {formatAmount(invoiceTotalAmount, selectedAgreement?.currency ?? inv.currency)}
                    </span>
                    <span className={cn('font-medium', slipVarianceIsZero ? 'text-success-700' : 'text-warning-700')}>
                      Difference {formatAmount(slipVarianceAmount, selectedAgreement?.currency ?? inv.currency)}
                    </span>
                  </div>
                  {/* Design decision 2: a non-zero difference asks for an
                      explanation but NEVER blocks submit — counter purchases
                      routinely differ from the slip total by freight,
                      discounts, or tax. Blocking here would just push the
                      operator to the no-evidence channel instead, which is
                      worse. */}
                  {!slipVarianceIsZero && (
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">
                        Explain the difference <span className="text-warning-700">(recommended)</span>
                      </label>
                      <textarea
                        rows={2}
                        value={slipVarianceReason}
                        onChange={(e) => setSlipVarianceReason(e.target.value)}
                        placeholder="e.g. Invoice includes freight not itemized on the counter slip."
                        className="px-3 py-2 rounded-lg border border-neutral-300 bg-white text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary-600"
                      />
                    </div>
                  )}
                </div>
              )}

              {selectedSlipIds.length === 0 && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    No pickup slips selected — reason for settling without receipt evidence <span className="text-danger-600">*</span>
                  </label>
                  <p className="text-[11px] text-neutral-500">
                    This invoice will be paid against the agreement with no pickup slip to reconcile against.
                    Explain why — this is recorded for audit.
                  </p>
                  <textarea
                    rows={3}
                    value={legacyReason}
                    onChange={(e) => setLegacyReason(e.target.value)}
                    placeholder="e.g. Monthly house-account statement for vendor counter pickups; pickup slips not yet digitized."
                    className="px-3 py-2 rounded-lg border border-neutral-300 bg-white text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary-600"
                  />
                </div>
              )}
            </div>
          )}

          {/* recurring — no reason field: the server FIFO-claims the next
              pending/overdue period itself (see claim_next_period). Preview
              which one, and warn (without blocking) if the total falls
              outside its tolerance. */}
          {isRecurring && (
            <>
              <RecurringPeriodPreview
                loading={scheduleLoading}
                error={scheduleErrored}
                row={nextPeriodRow}
                invoiceTotal={Number(inv.total_amount)}
                currency={selectedAgreement?.currency ?? inv.currency}
              />
              {/* Manual-assignment override (whole-branch review Blocker 2) —
                  only surfaced when the automatic claim can't be trusted.
                  Optional: submitting with nothing picked here still works
                  exactly as before. */}
              {showPeriodOverride && (
                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-neutral-700">
                      Assign a specific period instead (optional)
                    </label>
                    {selectedScheduleId && (
                      <button
                        type="button"
                        onClick={() => setSelectedScheduleId('')}
                        className="text-[11px] text-primary-600 hover:underline"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                  {unclaimedPeriodRows.length === 0 ? (
                    !scheduleErrored && (
                      <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
                        No open periods on this agreement.
                      </p>
                    )
                  ) : (
                    <div className="flex flex-col gap-2">
                      {unclaimedPeriodRows.map((row) => (
                        <PeriodOverrideRow
                          key={row.id}
                          row={row}
                          selected={selectedScheduleId === row.id}
                          onSelect={() => setSelectedScheduleId(selectedScheduleId === row.id ? '' : row.id)}
                          currency={selectedAgreement?.currency ?? inv.currency}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {/* milestone — no reason field, no amount check: expected vs actual
              is shown for a human to judge (Task 6 backend does no validation
              here). Only unclaimed stages (invoice_id === null) are listed. */}
          {isMilestone && (
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700">
                Which stage does this invoice pay for? <span className="text-danger-600">*</span>
              </label>
              {scheduleLoading ? (
                <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
                  Loading stages…
                </p>
              ) : scheduleErrored ? (
                // Whole-branch review Item 8: distinct from "no unclaimed
                // stages" — this is a permission gap, not an empty schedule.
                // Submit is not disabled (see canSubmitAgreement); the
                // backend's own "Pick the milestone stage" error surfaces
                // instead if they submit without one.
                <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2.5 text-xs text-warning-800">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <p>Couldn't load the stages for this agreement — you may not have permission to view them.</p>
                </div>
              ) : milestoneRows.length === 0 ? (
                <p className="rounded-lg border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">
                  No unclaimed stages on this agreement.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {milestoneRows.map((row) => (
                    <MilestoneStageRow
                      key={row.id}
                      row={row}
                      selected={selectedScheduleId === row.id}
                      onSelect={() => setSelectedScheduleId(row.id)}
                      currency={selectedAgreement?.currency ?? inv.currency}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="flex justify-end">
            <Button onClick={handleMatchAgreement} disabled={!canSubmitAgreement || matchInvoiceMutation.isPending}>
              {matchInvoiceMutation.isPending
                ? 'Matching...'
                : isHouseAccount && selectedSlipIds.length === 0
                ? 'Confirm legacy settlement & match'
                : 'Match to agreement'}
            </Button>
          </div>
        </div>
      )}

      {matchInvoiceMutation.isError && (
        <div className="flex items-center gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {matchInvoiceMutation.error instanceof Error ? matchInvoiceMutation.error.message : 'Match failed'}
        </div>
      )}

      {/* Assignee escape hatch — bounce the assignment back to the assigner */}
      {isMyAssignment && (
        declining ? (
          <div className="flex flex-col gap-2 rounded-lg border border-warning-200 bg-warning-50 p-3">
            <p className="text-xs font-medium text-warning-800">
              Decline this assignment — it will go back to the person who assigned it, with your note.
            </p>
            <textarea
              rows={2} autoFocus value={declineNote}
              onChange={(e) => setDeclineNote(e.target.value)}
              placeholder="Why can't you match this invoice? (required)"
              className="px-3 py-2 rounded-lg border border-neutral-300 bg-white text-xs resize-none focus:outline-none focus:ring-1 focus:ring-warning-500"
            />
            {declineMutation.isError && (
              <p className="text-xs text-danger-600">
                {declineMutation.error instanceof Error ? declineMutation.error.message : 'Decline failed'}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setDeclining(false)}>Back</Button>
              <Button size="sm" disabled={!declineNote.trim() || declineMutation.isPending} onClick={handleDecline}>
                {declineMutation.isPending ? 'Declining…' : 'Decline & Notify Assigner'}
              </Button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setDeclining(true)}
            className="self-start text-xs text-warning-700 underline-offset-2 hover:underline"
          >
            Can't match this invoice? Decline the assignment
          </button>
        )
      )}

      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
      </div>
    </div>
  )
}
