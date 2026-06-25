/**
 * DocumentChainTree — unified document-chain sidebar used on PR / PO / PA detail pages.
 *
 * Visual format (always the same):
 *   [PR card  — ancestor link or current-highlighted]
 *        ↓
 *   [PO card  — ancestor link or current-highlighted]
 *        └─ GR row(s)
 *        └─ Invoice row(s)
 *        └─ PA row(s)   ← current PA gets a ring highlight when on PA detail
 */
import { Link } from 'react-router-dom'
import {
  FileText, Package, Warehouse, CreditCard, Receipt,
  ArrowRight, ChevronRight,
} from 'lucide-react'
import { cn, formatAmount } from '@/lib/utils'
import { StatusBadge } from '@/components/ui/badge'
import { usePr } from '@/hooks/usePrs'
import { usePo } from '@/hooks/usePos'
import { usePa } from '@/hooks/usePas'
import { useGrs } from '@/hooks/useGrs'
import { useInvoices } from '@/hooks/useInvoices'
import { usePas } from '@/hooks/usePas'
import type { GrStatus } from '@/services/gr'
import type { InvoiceStatus } from '@/services/invoices'
import type { PaStatus } from '@/services/pa'
import type { DocumentStatus } from '@/types'

// ─── Status maps ──────────────────────────────────────────────────────────────

const GR_STATUS_LABELS: Record<GrStatus, string> = {
  pending_ack:        'Pending Ack.',
  collection_pending: 'Collection Pending',
  collected:          'Collected',
  confirmed:          'Confirmed',
  discrepancy:        'Discrepancy',
  cancelled:          'Cancelled',
}

const INV_STATUS_LABELS: Record<InvoiceStatus, string> = {
  unmatched: 'Unmatched',
  matched:   'Matched',
  exception: 'Exception',
  approved:  'Approved',
  paid:      'Paid',
}

const PA_STATUS_LABELS: Record<PaStatus, string> = {
  draft:     'Draft',
  submitted: 'Submitted',
  in_review: 'In Review',
  approved:  'Approved',
  processed: 'Processed',
  returned:  'Returned',
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
}

function grStatusToDoc(s: GrStatus): DocumentStatus {
  const map: Record<GrStatus, DocumentStatus> = {
    pending_ack: 'submitted', collection_pending: 'submitted',
    collected: 'collected', confirmed: 'confirmed',
    discrepancy: 'returned', cancelled: 'cancelled',
  }
  return map[s]
}

function invStatusToDoc(s: InvoiceStatus): DocumentStatus {
  return ({ unmatched: 'submitted', matched: 'matched', exception: 'returned', approved: 'approved', paid: 'paid' } as Record<InvoiceStatus, DocumentStatus>)[s]
}

function paStatusToDoc(s: PaStatus): DocumentStatus {
  return ({ draft: 'draft', submitted: 'submitted', in_review: 'in_review', approved: 'approved', processed: 'paid', returned: 'returned', cancelled: 'cancelled' } as Record<PaStatus, DocumentStatus>)[s]
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

/** Tree row — a child node hanging below the anchor with a side connector. */
function TreeRow({
  icon, label, number, meta, statusDoc, statusLabel, href, hrefExternal = false, isLast, isCurrent = false,
}: {
  icon: React.ReactNode
  label: string
  number?: string
  meta?: string
  statusDoc?: DocumentStatus
  statusLabel?: string
  href?: string
  hrefExternal?: boolean
  isLast: boolean
  isCurrent?: boolean
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
          {statusDoc && <StatusBadge status={statusDoc} label={statusLabel} />}
        </div>
        {meta && <p className="text-[10px] text-neutral-400 mt-0.5 truncate">{meta}</p>}
      </div>
      {href && !isCurrent && <ChevronRight className="h-3 w-3 shrink-0 text-neutral-300 group-hover:text-primary-400" />}
    </div>
  )

  return (
    <div className="flex gap-2 items-stretch">
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
  currentType: 'pr' | 'po' | 'pa'
  /** The ID of the current document. */
  id: string
}

export function DocumentChainTree({ currentType, id }: DocumentChainTreeProps) {
  // ── Fetch the "entry point" document ───────────────────────────────────────
  const { data: currentPr } = usePr(currentType === 'pr' ? id : '')
  const { data: currentPo } = usePo(currentType === 'po' ? id : '')
  const { data: currentPa } = usePa(currentType === 'pa' ? id : '')

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

  // ── Fetch PO children ──────────────────────────────────────────────────────
  const { data: grsData }      = useGrs({ po_id: poId }, !!poId)
  const { data: invoicesData } = useInvoices({ po_id: poId }, !!poId)
  const { data: pasData }      = usePas({ po_id: poId }, !!poId)

  const poGrs      = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')
  const poInvoices = invoicesData?.items ?? []
  const poPas      = pasData?.items ?? []
  const totalChildren = poGrs.length + poInvoices.length + poPas.length
  let childIndex = 0

  // ── Build render ───────────────────────────────────────────────────────────

  // What's shown as the anchor (highlighted ring card)?
  // PR detail → PR is anchor; PO/PA detail → PO is anchor
  const anchorIsPr = currentType === 'pr'

  return (
    <div className="mt-5 border-t border-neutral-100 pt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 mb-3">
        Document Chain
      </h3>

      {/* ── Ancestors above the anchor ─────────────────────────────────────── */}

      {/* PR ancestor (shown when PO or PA is the current page) */}
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

      {anchorIsPr && currentPr ? (
        <AnchorCard
          icon={<FileText className="h-3 w-3" />}
          iconColor="border-primary-200 bg-primary-50 text-primary-600"
          number={currentPr.number}
          meta={currentPr.title}
          status={currentPr.status as DocumentStatus}
        />
      ) : !anchorIsPr && po ? (
        <AnchorCard
          icon={<Package className="h-3 w-3" />}
          iconColor="border-success-300 bg-success-50 text-success-700"
          number={po.number}
          meta={po.vendor_name}
          status={po.status as DocumentStatus}
        />
      ) : null}

      {/* ── Children tree rows ─────────────────────────────────────────────── */}

      {/* On PR detail, PO is the first child */}
      {anchorIsPr && (() => {
        if (!po && !currentPr?.po_id) return null
        const idx = childIndex++
        const totalWithPo = totalChildren + (po || currentPr?.po_id ? 1 : 0)
        return (
          <TreeRow
            icon={<Package className="h-3 w-3" />}
            label="Purchase Order"
            number={po?.number ?? currentPr?.po_number}
            meta={po?.vendor_name}
            statusDoc={po ? po.status as DocumentStatus : undefined}
            statusLabel={po?.status ? (PO_STATUS_LABELS[po.status] ?? po.status) : undefined}
            href={po ? `/po/${po.id}` : undefined}
            isLast={idx === totalWithPo - 1}
          />
        )
      })()}

      {poGrs.map((gr) => {
        const idx = childIndex++
        return (
          <TreeRow
            key={gr.id}
            icon={<Warehouse className="h-3 w-3" />}
            label="Goods Receipt"
            number={gr.number}
            meta={`${gr.gr_type === 'physical' ? 'Physical' : 'Service'} · ${new Date(gr.received_at).toLocaleDateString('en-CA')}`}
            statusDoc={grStatusToDoc(gr.status)}
            statusLabel={GR_STATUS_LABELS[gr.status]}
            href={`/gr/${gr.id}`}
            isLast={idx === totalChildren - 1}
          />
        )
      })}

      {poInvoices.map((inv) => {
        const idx = childIndex++
        return (
          <TreeRow
            key={inv.id}
            icon={<Receipt className="h-3 w-3" />}
            label="Invoice"
            number={inv.internal_ref}
            meta={`${formatAmount(Number(inv.total_amount), inv.currency)} · ${inv.vendor_invoice_number}`}
            statusDoc={invStatusToDoc(inv.status)}
            statusLabel={INV_STATUS_LABELS[inv.status]}
            href={`/invoices/${inv.id}`}
            isLast={idx === totalChildren - 1}
          />
        )
      })}

      {poPas.map((pa) => {
        const idx = childIndex++
        const isCurrent = currentType === 'pa' && pa.id === id
        return (
          <TreeRow
            key={pa.id}
            icon={<CreditCard className="h-3 w-3" />}
            label="Payment Application"
            number={pa.pa_number}
            meta={`${formatAmount(pa.payment_amount, pa.currency)}${pa.pa_type === 'prepayment' ? ' · Prepayment' : ''}`}
            statusDoc={paStatusToDoc(pa.status)}
            statusLabel={PA_STATUS_LABELS[pa.status]}
            href={`/pa/${pa.id}`}
            isLast={idx === totalChildren - 1}
            isCurrent={isCurrent}
          />
        )
      })}

      {totalChildren === 0 && !anchorIsPr && (
        <div className="mt-2 pl-6">
          <p className="text-[11px] text-neutral-400 italic">No linked GRs, invoices, or payments yet</p>
        </div>
      )}
      {totalChildren === 0 && anchorIsPr && !po && !currentPr?.po_id && (
        <div className="mt-2 pl-6">
          <p className="text-[11px] text-neutral-400 italic">No linked PO, GRs, invoices, or payments yet</p>
        </div>
      )}
    </div>
  )
}
