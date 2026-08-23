/**
 * DocumentChainTree — unified document-chain sidebar used on PR / PO / PA /
 * Agreement detail pages.
 *
 * PO axis (PR / PO / PA-from-PO), visual format:
 *   [PR card  — ancestor link or current-highlighted]
 *        ↓
 *   [PO card  — ancestor link or current-highlighted]
 *        └─ GR row(s)
 *        └─ Invoice row(s)
 *             └─ PA row(s)   ← PAs nest under the invoice they were raised from,
 *                              so it's obvious which invoices already have a PA.
 *        └─ PA row(s)        ← PAs with no invoice on this PO (e.g. prepayment)
 *                              stay as a direct PO child. Current PA gets a ring.
 *
 * Agreement axis (Agreement / PA-from-agreement), Task 11 — a parallel pivot,
 * NOT a fallback of the PO axis above (PO-sourced PAs have po_id set;
 * agreement-sourced PAs have po_id NULL and agreement_id set instead, so
 * they resolve to a completely different anchor):
 *   [Agreement card — ancestor link or current-highlighted]
 *        └─ Invoice row(s)  ← GET /invoices?agreement_id=
 *             ├─ Receipt row(s)  ← invoice.receipt_ids (house_account only;
 *             │                     recurring/milestone invoices never carry any)
 *             └─ PA row(s)              ← same invoice-nesting convention as the PO axis
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  FileText, FileSignature, Package, Warehouse, CreditCard, Receipt, Ticket,
  ArrowRight, ChevronRight, Paperclip, AlertTriangle,
} from 'lucide-react'
import { cn, formatAmount } from '@/lib/utils'
import { StatusBadge } from '@/components/ui/badge'
import { ChainAttachmentsPanel } from '@/components/shared/ChainAttachmentsPanel'
import { usePr } from '@/hooks/usePrs'
import { usePo } from '@/hooks/usePos'
import { usePa } from '@/hooks/usePas'
import { useGrs } from '@/hooks/useGrs'
import { useInvoices } from '@/hooks/useInvoices'
import { usePas } from '@/hooks/usePas'
import { useAgreement } from '@/hooks/useAgreements'
import { useAgreementReceipts } from '@/hooks/useAgreementReceipts'
import { useRolePermissions } from '@/hooks/useConfig'
import { useAuthStore } from '@/stores/auth.store'
import type { GrStatus } from '@/services/gr'
import type { InvoiceStatus } from '@/services/invoices'
import type { ApiPa, PaStatus } from '@/services/pa'
import { RECEIPT_TYPE_LABELS, type ApiReceipt, type ReceiptStatus } from '@/services/agreementReceipts'
import type { DocumentStatus } from '@/types'

// ─── Status maps ──────────────────────────────────────────────────────────────

const GR_STATUS_LABELS: Record<GrStatus, string> = {
  pending_ack:        'Pending Ack.',
  collection_pending: 'Collection Pending',
  collected:          'Collected',
  confirmed:          'Confirmed',
  discrepancy:        'Discrepancy',
  rejected:           'Rejected',
  cancelled:          'Cancelled',
}

const INV_STATUS_LABELS: Record<InvoiceStatus, string> = {
  unmatched:    'Unmatched',
  matched:      'Matched',
  exception:    'Exception',
  match_review: 'Pending Review',
  approved:     'Approved',
  paid:         'Paid',
}

const PA_STATUS_LABELS: Record<PaStatus, string> = {
  draft:     'Draft',
  submitted: 'Submitted',
  in_review: 'In Review',
  approved:  'Approved',
  processed: 'Processed',
  returned:  'Returned',
  rejected:  'Rejected',
  cancelled: 'Cancelled',
}

const PO_STATUS_LABELS: Record<string, string> = {
  draft:             'Draft',
  submitted:         'Submitted',
  pending_approval:  'Pending Approval',
  approved:          'Approved',
  issued:            'Issued',
  partially_received:'Partially Received',
  fully_received:    'Fully Received',
  cancelled:         'Cancelled',
  closed:            'Closed',
  nc_milk:           'Milk / NC',
  nc_pending:        'NC Pending Approval',
}

function grStatusToDoc(s: GrStatus): DocumentStatus {
  const map: Record<GrStatus, DocumentStatus> = {
    pending_ack: 'submitted', collection_pending: 'submitted',
    collected: 'collected', confirmed: 'confirmed',
    discrepancy: 'returned', rejected: 'cancelled', cancelled: 'cancelled',
  }
  return map[s]
}

function invStatusToDoc(s: InvoiceStatus): DocumentStatus {
  return ({ unmatched: 'submitted', matched: 'matched', exception: 'returned', match_review: 'in_review', approved: 'approved', paid: 'paid' } as Record<InvoiceStatus, DocumentStatus>)[s]
}

function paStatusToDoc(s: PaStatus): DocumentStatus {
  return ({ draft: 'draft', submitted: 'submitted', in_review: 'in_review', approved: 'approved', processed: 'paid', returned: 'returned', rejected: 'cancelled', cancelled: 'cancelled' } as Record<PaStatus, DocumentStatus>)[s]
}

// Fix round 1 (Minor 2): an explicit mapping, same convention as the three
// functions above — a bare `r.status as DocumentStatus` cast would let a
// future backend-added ReceiptStatus value compile silently and fall through
// to StatusBadge's raw-snake_case fallback at runtime instead of failing here.
function receiptStatusToDoc(s: ReceiptStatus): DocumentStatus {
  return ({ pending_ap_review: 'pending_ap_review', open: 'open', reconciled: 'reconciled', voided: 'voided', rejected: 'rejected' } as Record<ReceiptStatus, DocumentStatus>)[s]
}

// Receipt row label — never a bare UUID (branch owner's ruling). Same
// fallback ApiReceipt has no currency of its own, so the meta text this feeds
// is formatted with the AGREEMENT's currency at the call site, never a
// hardcoded one.
function receiptRowLabel(r: ApiReceipt): string {
  // Falls back to date alone when the receipt carries no amount (ag09):
  // "2026-08-04 · 0.00" would read as a zero-value receipt.
  if (r.receipt_ref) return r.receipt_ref
  return r.total_amount === null
    ? r.receipt_date
    : `${r.receipt_date} · ${Number(r.total_amount).toFixed(2)}`
}

// ─── Sub-components ───────────────────────────────────────────────────────────

/** Full-width ancestor card shown above the anchor node. */
function AncestorCard({
  icon, number, meta, href, status,
}: {
  icon: React.ReactNode
  number: string
  meta: string
  href: string
  status?: DocumentStatus
}) {
  return (
    <div className="mb-1">
      <Link to={href}>
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 hover:border-primary-300 hover:bg-primary-50/50 transition-colors group">
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-primary-200 bg-primary-50 text-primary-600">
            {icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-1.5">
              <span className="font-mono text-[11px] font-semibold text-neutral-700 truncate">{number}</span>
              {status && <StatusBadge status={status} />}
            </div>
            <p className="text-[10px] text-neutral-400 mt-0.5 truncate">{meta}</p>
          </div>
          <ArrowRight className="h-3.5 w-3.5 shrink-0 text-neutral-300 group-hover:text-primary-400" />
        </div>
      </Link>
      <div className="flex justify-center py-0.5">
        <div className="w-px h-3 bg-neutral-200" />
      </div>
    </div>
  )
}

/** Highlighted anchor node (current page's document or its parent PO). */
function AnchorCard({
  icon, iconColor, number, meta, status,
}: {
  icon: React.ReactNode
  iconColor: string
  number: string
  meta: string
  status?: DocumentStatus
}) {
  return (
    <div className="rounded-lg border border-primary-300 bg-primary-50 ring-1 ring-primary-200 px-3 py-2.5 flex items-center gap-2">
      <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded border', iconColor)}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-1.5">
          <span className="font-mono text-[11px] font-semibold text-primary-700 truncate">{number}</span>
          {status && <StatusBadge status={status} />}
        </div>
        <p className="text-[10px] text-neutral-500 mt-0.5 truncate">{meta}</p>
      </div>
    </div>
  )
}

/** Tree row — a child node hanging below the anchor with a side connector.
 *  `ancestorLines` draws one indent column per ancestor level; each entry says
 *  whether that ancestor's vertical line should continue past this row (i.e. the
 *  ancestor has more siblings below). This lets PA rows nest under an invoice. */
function TreeRow({
  icon, label, number, meta, statusDoc, statusLabel, extraBadge, href, hrefExternal = false, isLast, isCurrent = false, ancestorLines = [],
}: {
  icon: React.ReactNode
  label: string
  number?: string
  meta?: string
  statusDoc?: DocumentStatus
  statusLabel?: string
  extraBadge?: React.ReactNode
  href?: string
  hrefExternal?: boolean
  isLast: boolean
  isCurrent?: boolean
  ancestorLines?: boolean[]
}) {
  const inner = (
    <div className={cn(
      'flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors text-xs group',
      isCurrent
        ? 'border-primary-300 bg-primary-50 ring-1 ring-primary-200'
        : href
        ? 'border-neutral-200 bg-white hover:border-primary-300 hover:bg-primary-50/50 cursor-pointer'
        : 'border-neutral-100 bg-neutral-50 opacity-60',
    )}>
      <div className={cn(
        'flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-colors',
        isCurrent
          ? 'border-primary-200 bg-primary-50 text-primary-600'
          : href
          ? 'border-neutral-200 bg-white text-neutral-400 group-hover:border-primary-200 group-hover:text-primary-600'
          : 'border-neutral-200 bg-neutral-100 text-neutral-300',
      )}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 justify-between">
          <span className={cn(
            'font-mono text-[11px] font-semibold truncate',
            isCurrent ? 'text-primary-700' : number ? 'text-neutral-700' : 'text-neutral-400',
          )}>
            {number ?? <span className="italic font-sans font-normal text-neutral-400">{label}</span>}
          </span>
          <div className="flex items-center gap-1 shrink-0">
            {extraBadge}
            {statusDoc && <StatusBadge status={statusDoc} label={statusLabel} />}
          </div>
        </div>
        {meta && <p className="text-[10px] text-neutral-400 mt-0.5 truncate">{meta}</p>}
      </div>
      {href && !isCurrent && <ChevronRight className="h-3 w-3 shrink-0 text-neutral-300 group-hover:text-primary-400" />}
    </div>
  )

  return (
    <div className="flex gap-2 items-stretch">
      {ancestorLines.map((cont, i) => (
        <div key={i} className="flex justify-center w-4 shrink-0">
          {cont && <div className="w-px bg-neutral-200 flex-1" />}
        </div>
      ))}
      <div className="flex flex-col items-center w-4 shrink-0">
        <div className="w-px bg-neutral-200 flex-1" />
        {isLast && <div className="w-px flex-1" />}
      </div>
      <div className="flex items-center flex-1 py-0.5">
        <div className="w-3 h-px bg-neutral-200 shrink-0" />
        <div className="flex-1">
          {href && !isCurrent
            ? hrefExternal
              ? <a href={href} target="_blank" rel="noopener noreferrer">{inner}</a>
              : <Link to={href}>{inner}</Link>
            : inner}
        </div>
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export interface DocumentChainTreeProps {
  /** Which document type this detail page is showing. */
  currentType: 'pr' | 'po' | 'pa' | 'agr'
  /** The ID of the current document. */
  id: string
}

export function DocumentChainTree({ currentType, id }: DocumentChainTreeProps) {
  // ── Fetch the "entry point" document ───────────────────────────────────────
  const { data: currentPr } = usePr(currentType === 'pr' ? id : '')
  const { data: currentPo } = usePo(currentType === 'po' ? id : '')
  const { data: currentPa } = usePa(currentType === 'pa' ? id : '')
  const { data: currentAgreement } = useAgreement(currentType === 'agr' ? id : '')

  // ── Resolve the PO id (pivot for all child fetches) ────────────────────────
  const poId: string =
    currentType === 'po' ? id :
    currentType === 'pr' ? (currentPr?.po_id ?? '') :
    (currentPa?.po_id ?? '')

  // ── Fetch ancestors we don't have yet ──────────────────────────────────────
  const { data: ancestorPo } = usePo(currentType !== 'po' && poId ? poId : '')
  const po = currentType === 'po' ? currentPo : ancestorPo

  const prId: string =
    currentType === 'pr' ? id :
    (po?.pr_id ?? '')
  const { data: ancestorPr } = usePr(currentType !== 'pr' && prId ? prId : '')
  const pr = currentType === 'pr' ? currentPr : ancestorPr

  // ── Resolve the agreement id (pivot for the agreement axis) ────────────────
  // PO-pivot fix: house_account / recurring / milestone PAs raised off an
  // agreement (Phase 1A/1B) always have po_id NULL. The formula above already
  // resolves poId to '' for them (currentPa?.po_id ?? '' → ''), which used to
  // disable every single child query on this page — opening any agreement PA's
  // detail page rendered a totally empty Document Chain. This mirrors poId's
  // shape one level up: resolve back to the agreement via pa.agreement_id.
  const agreementId: string =
    currentType === 'agr' ? id :
    (currentPa?.agreement_id ?? '')
  const { data: ancestorAgreement } = useAgreement(currentType !== 'agr' && agreementId ? agreementId : '')
  const agreement = currentType === 'agr' ? currentAgreement : ancestorAgreement

  // ── Fetch PO children ──────────────────────────────────────────────────────
  const { data: grsData }      = useGrs({ po_id: poId }, !!poId)
  const { data: invoicesData } = useInvoices({ po_id: poId }, !!poId)
  const { data: pasData }      = usePas({ po_id: poId }, !!poId)

  const poGrs      = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')
  const poInvoices = invoicesData?.items ?? []
  const poPas      = pasData?.items ?? []

  // ── Nest PAs under the invoice(s) they were raised from ────────────────────
  // A PA derives from its invoice_ids, so show it as a child of each linked
  // invoice on this PO — this makes it obvious which invoices already have a PA
  // and which are still awaiting one. PAs with no invoice on this PO (e.g. a
  // prepayment PA raised straight off the PO) stay as a direct PO child.
  const invoiceIdSet = new Set(poInvoices.map((i) => i.id))
  const pasByInvoice = new Map<string, typeof poPas>()
  const orphanPas: typeof poPas = []
  for (const pa of poPas) {
    const linked = (pa.invoice_ids ?? []).filter((iid) => invoiceIdSet.has(iid))
    if (linked.length === 0) {
      orphanPas.push(pa)
    } else {
      for (const iid of linked) {
        const arr = pasByInvoice.get(iid) ?? []
        arr.push(pa)
        pasByInvoice.set(iid, arr)
      }
    }
  }
  const totalChildren = poGrs.length + poInvoices.length + poPas.length

  // ── Fetch the agreement axis's children ─────────────────────────────────────
  // Which entity anchors the tree (the highlighted ring card)? PR for a PR
  // page; PO for a PO page OR a PO-sourced PA page (po_id set); the Agreement
  // itself for an Agreement page OR an agreement-sourced PA page (po_id null,
  // agreement_id set) — that last case is the fix described above.
  const anchorKind: 'pr' | 'po' | 'agr' =
    currentType === 'pr' ? 'pr' :
    currentType === 'po' ? 'po' :
    currentType === 'agr' ? 'agr' :
    currentPa?.po_id ? 'po' : 'agr'
  const isAgrAxis = anchorKind === 'agr'

  // GET /invoices?agreement_id= — backed by the same query param AgreementDetailPage
  // already uses (services/invoices.ts InvoiceFilters.agreement_id, epms-api's
  // api/v1/invoices.py:206 / crud/invoice.py get_all).
  const { data: agrInvoicesData } = useInvoices({ agreement_id: agreementId }, isAgrAxis && !!agreementId)
  const agrInvoices = agrInvoicesData?.items ?? []

  // Whole-branch review (I1): GET /invoices returns 200 with an EMPTY list —
  // not 403 — when the caller's Access Control Matrix row has view_invoice
  // off (api/v1/invoices.py list_invoices). The default matrix gives
  // `director` view_pa and (via identity 0006) epms.agreement.read, but NOT
  // view_invoice. So a Director opening a house_account agreement — or the
  // agreement PA they are about to approve — got a chain that positively
  // asserted "No invoices matched to this agreement yet" with 20 invoices
  // underneath it, and no receipt layer either (receipts hang off invoices),
  // and no error to hint at it because the request succeeded. An approver
  // reading "the evidence chain is empty" and approving payment on that
  // basis is the failure this closes.
  //
  // Frontend-only on purpose: making list_invoices 403 instead would change
  // the contract for every other caller of that endpoint. Read the same
  // matrix key the endpoint gates on, and — only once the permission query
  // has actually resolved — say the layer is hidden rather than claiming it
  // is empty. system_admin override mirrors Sidebar.tsx's isItemVisible.
  const { user } = useAuthStore()
  const permsQuery = useRolePermissions()
  const canViewInvoices =
    user?.role === 'system_admin' || !!permsQuery.data?.permissions?.['view_invoice']
  // Never render the "hidden" note off an unresolved query — during the
  // in-flight frame `permissions` is undefined for everyone, including the
  // people who can see invoices fine.
  const invoiceLayerHidden = isAgrAxis && permsQuery.isSuccess && !canViewInvoices

  // GET /pa has no agreement_id filter (only status/po_id/vendor_id/department_id/
  // search — epms-api/app/api/v1/pa.py:147-158), unlike /invoices. `search`
  // already matches PaymentApplication.agreement_number (crud/pa.py:123, a
  // snapshot column set at PA-create time) so it narrows the fetch at the DB —
  // but the EXACT filter that actually guarantees correctness is the
  // client-side pa.agreement_id === agreementId check below (ApiPa carries its
  // own agreement_id — services/pa.ts:74). If `search` ever stops matching
  // agreement_number this just degrades to fetching more rows, never to a
  // wrong result.
  //
  // Fix round 1 (Important a): the search TERM must not depend on `agreement`
  // (GET /agreements/{id}, gated on epms.agreement.read — 15 roles, NOT
  // including warehouse_staff/supervisor/vendor_manager). On a PA page,
  // currentPa.agreement_number is a snapshot column on the PA response itself
  // (schemas/pa.py:156, no agreement-read needed) — prefer it, and only fall
  // back to `agreement?.number` on the Agreement page itself (currentType
  // 'agr'), where `agreement` already loaded successfully by definition (the
  // page couldn't have rendered agreement.id otherwise). This decouples the
  // PA row layer from agreement-read entirely, so a PA the caller can already
  // see (epms.pa.read) always shows up in its own chain.
  const agrPaSearchTerm =
    currentType === 'agr' ? agreement?.number : (currentPa?.agreement_number ?? agreement?.number)
  const { data: agrPasData } = usePas(
    { search: agrPaSearchTerm },
    isAgrAxis && !!agreementId && !!agrPaSearchTerm,
  )
  const agrPas = (agrPasData?.items ?? []).filter((p) => p.agreement_id === agreementId)

  // Fix round 1 (Important b): don't gate this fetch on `agreement` having
  // loaded — that would make it silently never fire (and never surface
  // agrReceiptsError below) for exactly the callers missing epms.agreement.read,
  // the same permission GET /agreements/{id}/receipts itself requires
  // (agreement_receipts.py ReceiptReadDep = require_permission("epms.agreement.read")).
  // Only SKIP the fetch once we positively know (from an already-loaded
  // `agreement`) that this is a recurring/milestone agreement, which never has
  // receipt rows at all — the original perf optimization, preserved for the
  // case where it doesn't hide a permission gap. While `agreement` is still
  // unresolved (loading OR 403), attempt the fetch anyway: it'll either
  // succeed (this agreement axis didn't even need agreement-read) or fail,
  // and `isError` below drives an explicit "no permission" note instead of a
  // silent empty layer.
  const skipReceiptsFetch = agreement != null && agreement.agreement_type !== 'house_account'
  const { data: agrReceiptsData, isError: agrReceiptsError } = useAgreementReceipts(
    isAgrAxis && agreementId && !skipReceiptsFetch ? agreementId : ''
  )
  const agrReceiptsById = new Map<string, ApiReceipt>((agrReceiptsData?.items ?? []).map((r) => [r.id, r]))

  // Nest PAs under the invoice(s) they were raised from — same convention as
  // pasByInvoice above, INCLUDING the orphan bucket.
  //
  // Fix round 1 (Critical): _validate_agreement_pa_invoices guarantees every
  // agreement-route PA carries >=1 invoice_id matched to this agreement — but
  // only on the BACKEND's own read of that invoice. It says nothing about
  // whether THIS caller's `agrInvoices` (scoped GET /invoices?agreement_id=)
  // actually contains that invoice. Two ways it can legitimately not:
  //   1. crud/invoice.py get_all's scope OR-block (po_ids_subq / own_uploads /
  //      task_user_id) has no agreement-scope branch at all, so a restricted
  //      role (requester/dept_manager/dept_admin/gm/opm/supervisor/director)
  //      viewing a PA they hold an approval task for gets an EMPTY invoice
  //      list back for this agreement, even though their own PA is real.
  //   2. crud/invoice.py:398 nulls invoice.agreement_id when an invoice gets
  //      re-matched onto the PO route — a PA already raised against that
  //      invoice stays valid, but the invoice silently drops out of
  //      `?agreement_id=` from then on.
  // Either way, without this bucket the PA the caller is ACTUALLY looking at
  // could vanish from its own chain. Mirrors the PO axis's orphanPas exactly.
  const agrInvoiceIdSet = new Set(agrInvoices.map((i) => i.id))
  const agrPasByInvoice = new Map<string, typeof agrPas>()
  const agrOrphanPas: typeof agrPas = []
  for (const pa of agrPas) {
    const linked = (pa.invoice_ids ?? []).filter((iid) => agrInvoiceIdSet.has(iid))
    if (linked.length === 0) {
      agrOrphanPas.push(pa)
    } else {
      for (const iid of linked) {
        const arr = agrPasByInvoice.get(iid) ?? []
        arr.push(pa)
        agrPasByInvoice.set(iid, arr)
      }
    }
  }

  // ── Build render ───────────────────────────────────────────────────────────

  // What's shown as the anchor (highlighted ring card)?
  // PR detail → PR is anchor; PO/PA(PO-sourced) detail → PO is anchor;
  // Agreement/PA(agreement-sourced) detail → Agreement is anchor.
  const anchorIsPr = anchorKind === 'pr'

  // Fix round 1 (Minor 1): while a PA page's own entry document hasn't loaded
  // yet, currentPa is undefined, so currentPa?.po_id reads as falsy and
  // anchorKind falls through to its 'agr' default — for one render, a
  // PO-sourced PA page would flash the agreement axis's "No invoices matched…"
  // empty state (wrong content, not just early). Suppress every empty-state
  // line below during that specific window.
  const paEntryStillLoading = currentType === 'pa' && !currentPa

  const [showAttachments, setShowAttachments] = useState(false)
  const isPa = currentType === 'pa'

  // Shared PA row renderer — used by both the PO axis (below) and the
  // agreement axis (further down). Lifted out of the PO axis's render closure
  // so it can be reused verbatim; behaviour for PO-sourced PAs is unchanged.
  const paRow = (pa: ApiPa, key: string, isLast: boolean, nested: boolean, parentContinues: boolean) => (
    <TreeRow
      key={key}
      icon={<CreditCard className="h-3 w-3" />}
      label="Payment Application"
      number={pa.pa_number}
      meta={`${formatAmount(Number(pa.payment_amount), pa.currency)}${pa.pa_type === 'prepayment' ? ' · Prepayment' : ''}`}
      statusDoc={paStatusToDoc(pa.status)}
      statusLabel={PA_STATUS_LABELS[pa.status]}
      href={`/pa/${pa.id}`}
      isLast={isLast}
      isCurrent={currentType === 'pa' && pa.id === id}
      ancestorLines={nested ? [parentContinues] : []}
    />
  )

  return (
    <div className="mt-5 border-t border-neutral-100 pt-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Document Chain
        </h3>
        {isPa && currentPa && (
          <button
            type="button"
            onClick={() => setShowAttachments(true)}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-200 px-2 py-1 text-[11px] font-medium text-neutral-600 hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 transition-colors"
          >
            <Paperclip className="h-3 w-3" /> Attachments
          </button>
        )}
      </div>

      {isPa && currentPa && showAttachments && (
        <ChainAttachmentsPanel
          paId={currentPa.id}
          paNumber={currentPa.pa_number}
          onClose={() => setShowAttachments(false)}
        />
      )}

      {/* ── Ancestors above the anchor ─────────────────────────────────────── */}

      {/* PR ancestor (shown when PO or PA is the current page) — the agreement
          axis has no PR/PO ancestor at all, so `pr` stays unresolved there
          and this simply never renders for it. */}
      {!anchorIsPr && pr && (
        <AncestorCard
          icon={<FileText className="h-3 w-3" />}
          number={pr.number}
          meta="Purchase Requisition"
          href={`/pr/${pr.id}`}
          status={pr.status as DocumentStatus}
        />
      )}

      {/* ── Anchor node (highlighted ring) ────────────────────────────────── */}

      {anchorKind === 'pr' && currentPr ? (
        <AnchorCard
          icon={<FileText className="h-3 w-3" />}
          iconColor="border-primary-200 bg-primary-50 text-primary-600"
          number={currentPr.number}
          meta={currentPr.title}
          status={currentPr.status as DocumentStatus}
        />
      ) : anchorKind === 'po' && po ? (
        <AnchorCard
          icon={<Package className="h-3 w-3" />}
          iconColor="border-success-300 bg-success-50 text-success-700"
          number={po.number}
          meta={po.vendor_name}
          status={po.status as DocumentStatus}
        />
      ) : anchorKind === 'agr' && agreement ? (
        <AnchorCard
          icon={<FileSignature className="h-3 w-3" />}
          iconColor="border-warning-300 bg-warning-50 text-warning-700"
          number={agreement.number}
          meta={agreement.vendor_name}
          status={agreement.status as DocumentStatus}
        />
      ) : null}

      {/* ── Children tree rows ─────────────────────────────────────────────── */}

      {(() => {
        // Ordered top-level children: [PO (on PR detail)] · GRs · Invoices · orphan PAs.
        // Each invoice renders its linked PAs nested one level below it.
        const showPoRow = anchorIsPr && (po || !!currentPr?.po_id)
        const topCount = (showPoRow ? 1 : 0) + poGrs.length + poInvoices.length + orphanPas.length
        const last = topCount - 1
        const rows: React.ReactNode[] = []
        let t = 0

        if (showPoRow) {
          const idx = t++
          rows.push(
            <TreeRow
              key="po-child"
              icon={<Package className="h-3 w-3" />}
              label="Purchase Order"
              number={po?.number ?? currentPr?.po_number}
              meta={po?.vendor_name}
              statusDoc={po ? (po.status as DocumentStatus) : undefined}
              statusLabel={po?.status ? (PO_STATUS_LABELS[po.status] ?? po.status) : undefined}
              href={po ? `/po/${po.id}` : undefined}
              isLast={idx === last}
            />,
          )
        }

        for (const gr of poGrs) {
          const idx = t++
          rows.push(
            <TreeRow
              key={gr.id}
              icon={<Warehouse className="h-3 w-3" />}
              label="Goods Receipt"
              number={gr.number}
              meta={`${gr.gr_type === 'physical' ? 'Physical' : 'Service'} · ${new Date(gr.received_at).toLocaleDateString('en-CA')}`}
              statusDoc={grStatusToDoc(gr.status)}
              statusLabel={GR_STATUS_LABELS[gr.status]}
              extraBadge={gr.notes?.includes('NC Paid') ? <StatusBadge status="paid" label="Paid" /> : undefined}
              href={`/gr/${gr.id}`}
              isLast={idx === last}
            />,
          )
        }

        for (const inv of poInvoices) {
          const idx = t++
          const childPas = pasByInvoice.get(inv.id) ?? []
          const parentContinues = idx < last // more top-level nodes after this invoice
          rows.push(
            <TreeRow
              key={inv.id}
              icon={<Receipt className="h-3 w-3" />}
              label="Invoice"
              number={inv.internal_ref}
              meta={`${formatAmount(Number(inv.total_amount), inv.currency)} · ${inv.vendor_invoice_number}`}
              statusDoc={invStatusToDoc(inv.status)}
              statusLabel={INV_STATUS_LABELS[inv.status]}
              href={`/invoices/${inv.id}`}
              isLast={idx === last && childPas.length === 0}
            />,
          )
          childPas.forEach((pa, j) =>
            rows.push(paRow(pa, `${inv.id}-${pa.id}`, j === childPas.length - 1, true, parentContinues)),
          )
        }

        for (const pa of orphanPas) {
          const idx = t++
          rows.push(paRow(pa, pa.id, idx === last, false, false))
        }

        return rows
      })()}

      {/* ── Agreement-axis children ──────────────────────────────────────────
          [Invoice(s)] ← GET /invoices?agreement_id=
               ├─ Receipt row(s)        ← invoice.receipt_ids, resolved off
               │                          this agreement's receipt list
               └─ PA row(s)             ← nested under the invoice they were
                                           raised from, same convention as the
                                           PO axis above. recurring/milestone
                                           agreements simply have no receipts
                                           to nest — the invoice/PA levels
                                           still render normally.
          Fix round 1 (Critical): orphan PAs (agrOrphanPas — see the comment
          where it's built) render as direct children of the Agreement, same
          as the PO axis's orphanPas — so a PA the caller can see is never
          missing from its own chain just because the invoice list didn't
          come back with it. */}
      {isAgrAxis && (() => {
        const topCount = agrInvoices.length + agrOrphanPas.length
        const last = topCount - 1
        const rows: React.ReactNode[] = []
        let t = 0

        // Fix round 2 (I1): see invoiceLayerHidden's definition — an empty
        // invoice list from a caller without view_invoice means "hidden",
        // never "none exist".
        if (invoiceLayerHidden) {
          rows.push(
            <div key="agr-invoices-perm-note" className="mb-2 pl-1 flex items-center gap-1.5 text-[11px] text-neutral-400 italic">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              Invoices on this agreement aren't visible with your permissions — this chain, and any receipt evidence hanging off those invoices, is incomplete.
            </div>,
          )
        }

        // Fix round 1 (Important b): don't fail silently — an explanatory row
        // instead of a receipt layer that's just never there.
        if (agrReceiptsError) {
          rows.push(
            // Whole-branch review (T11-★): the trigger is react-query's bare
            // `isError`, which does not distinguish 403 from a transient 5xx,
            // a timeout, or a dropped connection. The old copy asserted "you
            // don't have permission", which is simply false on every one of
            // those other paths — and "the UI states something it cannot
            // know" is the defect class this branch keeps re-introducing.
            // Say only what is actually established: the layer did not load,
            // so what's shown may be incomplete.
            <div key="agr-receipts-load-note" className="mb-2 pl-1 flex items-center gap-1.5 text-[11px] text-neutral-400 italic">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              This agreement's receipt evidence couldn't be loaded (you may not have access to it) — the chain below may be incomplete.
            </div>,
          )
        }

        for (const inv of agrInvoices) {
          const idx = t++
          const childReceipts = (inv.receipt_ids ?? [])
            .map((rid) => agrReceiptsById.get(rid))
            .filter((r): r is ApiReceipt => !!r)
          const childPas = agrPasByInvoice.get(inv.id) ?? []
          const childCount = childReceipts.length + childPas.length
          const parentContinues = idx < last // more top-level nodes after this invoice

          rows.push(
            <TreeRow
              key={inv.id}
              icon={<Receipt className="h-3 w-3" />}
              label="Invoice"
              number={inv.internal_ref}
              meta={`${formatAmount(Number(inv.total_amount), inv.currency)} · ${inv.vendor_invoice_number}`}
              statusDoc={invStatusToDoc(inv.status)}
              statusLabel={INV_STATUS_LABELS[inv.status]}
              href={`/invoices/${inv.id}`}
              isLast={idx === last && childCount === 0}
            />,
          )

          childReceipts.forEach((r, j) => {
            rows.push(
              <TreeRow
                key={`${inv.id}-receipt-${r.id}`}
                icon={<Ticket className="h-3 w-3" />}
                // Whole-branch review (M8): the label is the receipt's OWN
                // type, never the hardcoded "Pickup Receipt" this used to
                // print over a `delivery` note or a `service` sign-off. The
                // type moved out of `meta` (where it used to be duplicated)
                // and into the label slot, so the row now reads
                // "Delivery note · REF · $x" instead of
                // "Pickup Receipt · REF · Delivery note · $x".
                label={RECEIPT_TYPE_LABELS[r.receipt_type] ?? 'Receipt'}
                number={receiptRowLabel(r)}
                // Currency is the AGREEMENT's — ApiReceipt carries no currency
                // field of its own (see services/agreementReceipts.ts), and
                // this branch already only renders once `agreement` (and thus
                // agreement.currency) is loaded.
                meta={formatAmount(Number(r.total_amount), agreement?.currency ?? '')}
                statusDoc={receiptStatusToDoc(r.status)}
                href={`/agreements/${agreementId}`}
                isLast={j === childReceipts.length - 1 && childPas.length === 0}
                ancestorLines={[parentContinues]}
              />,
            )
          })

          childPas.forEach((pa, j) =>
            rows.push(paRow(pa, `${inv.id}-${pa.id}`, j === childPas.length - 1, true, parentContinues)),
          )
        }

        for (const pa of agrOrphanPas) {
          const idx = t++
          rows.push(paRow(pa, pa.id, idx === last, false, false))
        }

        return rows
      })()}

      {!paEntryStillLoading && totalChildren === 0 && anchorKind === 'po' && (
        <div className="mt-2 pl-6">
          <p className="text-[11px] text-neutral-400 italic">No linked GRs, invoices, or payments yet</p>
        </div>
      )}
      {!paEntryStillLoading && totalChildren === 0 && anchorIsPr && !po && !currentPr?.po_id && (
        <div className="mt-2 pl-6">
          <p className="text-[11px] text-neutral-400 italic">No linked PO, GRs, invoices, or payments yet</p>
        </div>
      )}
      {/* Fix round 2 (I1): this sentence is an ASSERTION about the data, so it
          must not be printed by a caller who was never allowed to see the
          data — the note rendered above says what's actually true for them. */}
      {!paEntryStillLoading && isAgrAxis && !invoiceLayerHidden && agrInvoices.length === 0 && (
        <div className="mt-2 pl-6">
          <p className="text-[11px] text-neutral-400 italic">No invoices matched to this agreement yet</p>
        </div>
      )}
    </div>
  )
}
