import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAgreements } from '@/hooks/useAgreements'
import { ReceiptEntryForm } from '@/components/agreements/ReceiptEntryForm'
import { RECEIPT_TYPE_LABELS, type ReceiptType } from '@/services/agreementReceipts'
import type { ApiAgreement } from '@/services/agreement'

// Mirrors AgreementDetailPage's isAgreementAdmissible (same file has the full
// rationale). Deliberately duplicated rather than imported — that copy lives
// alongside the Create-PA gate it was written for and isn't exported; keeping
// a second, identically-reasoned copy here is simpler and less coupling than
// exporting a helper across two unrelated pages for one boolean.
//
// Backend's create_receipt route (epms-api/app/api/v1/agreement_receipts.py)
// does NOT check agreement status at all — it will happily accept a POST
// against a draft/cancelled/past-grace agreement. A receipt recorded there
// can never be matched to an invoice (nothing routes an invoice to a
// non-admissible agreement) yet still counts toward the 45-day aging warning
// (ReceiptTable.RECEIPT_AGING_DAYS) forever. Filtering the picker to
// admissible agreements only is what keeps that from happening.
function isAgreementAdmissible(agreement: ApiAgreement): boolean {
  if (agreement.status === 'active') return true
  if (agreement.status !== 'expired') return false
  const daysSinceExpiry = Math.floor(
    (Date.now() - new Date(agreement.valid_to).getTime()) / 86_400_000
  )
  return daysSinceExpiry <= agreement.grace_days
}

const TYPE_OPTIONS: ReceiptType[] = ['counter_slip', 'delivery', 'service']

// ─── Page ─────────────────────────────────────────────────────────────────────

// Skeleton (breadcrumb / cards / submit-cancel placement) copied from
// GrCreatePage.tsx per the Task 10 brief — receipts get their own create
// page the same way GR does, instead of being entered from inside the
// agreement's detail page.
export default function ReceiptCreatePage() {
  const [searchParams] = useSearchParams()
  // No page/page_size in the filter → useAgreements pages through the full
  // result set itself (server default-truncates to 20 rows otherwise; see
  // hooks/useAgreements.ts), so this picker isn't silently missing agreements
  // past the first page.
  const { data: agreementsData } = useAgreements({ agreement_type: 'house_account' })
  const admissibleAgreements = (agreementsData?.items ?? []).filter(isAgreementAdmissible)

  const preselectedAgreementId = searchParams.get('agreement_id') ?? ''
  const [selectedAgreementId, setSelectedAgreementId] = useState(preselectedAgreementId)
  const [agreementSearch, setAgreementSearch] = useState('')
  const [showAgreementDropdown, setShowAgreementDropdown] = useState(false)
  const [receiptType, setReceiptType] = useState<ReceiptType>('counter_slip')

  const selectedAgreement: ApiAgreement | null =
    admissibleAgreements.find((a) => a.id === selectedAgreementId) ?? null

  // Came here from an agreement's detail page ("New Receipt" button) →
  // Cancel should return there rather than to the cross-agreement list.
  const cancelHref = preselectedAgreementId ? `/agreements/${preselectedAgreementId}` : '/receipts'

  const filteredAgreements = admissibleAgreements.filter((a) => {
    if (!agreementSearch) return true
    const q = agreementSearch.toLowerCase()
    return (
      a.number.toLowerCase().includes(q) ||
      a.vendor_name.toLowerCase().includes(q) ||
      a.title.toLowerCase().includes(q)
    )
  })

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/receipts" className="text-neutral-400 hover:text-neutral-600 transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New Agreement Receipt</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Record a counter slip, delivery note, or service sign-off against a house-account agreement
          </p>
        </div>
      </div>

      {/* Agreement Selection */}
      <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-4">
        <h2 className="text-base font-semibold text-neutral-900">Agreement</h2>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-neutral-700">
            Select Agreement <span className="text-danger-600">*</span>
          </label>
          <div className="relative">
            <input
              type="text"
              placeholder="Search by agreement number, vendor, or title..."
              value={
                selectedAgreement
                  ? `${selectedAgreement.number} — ${selectedAgreement.vendor_name} — ${selectedAgreement.title}`
                  : agreementSearch
              }
              onFocus={() => { if (!selectedAgreement) setShowAgreementDropdown(true) }}
              onChange={(e) => {
                setAgreementSearch(e.target.value)
                setSelectedAgreementId('')
                setShowAgreementDropdown(true)
              }}
              className="w-full h-10 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
            {selectedAgreement && (
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                onClick={() => { setSelectedAgreementId(''); setAgreementSearch(''); setShowAgreementDropdown(true) }}
              >
                ×
              </button>
            )}
            {showAgreementDropdown && !selectedAgreement && (
              <div className="absolute z-10 mt-1 w-full rounded-lg border border-neutral-200 bg-white shadow-lg max-h-64 overflow-y-auto">
                {filteredAgreements.length === 0 ? (
                  <div className="px-4 py-3 text-sm text-neutral-400">
                    No eligible house-account agreements found (must be active, or expired within its grace period)
                  </div>
                ) : (
                  filteredAgreements.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      className="w-full text-left px-4 py-3 hover:bg-primary-50 border-b border-neutral-100 last:border-0"
                      onClick={() => {
                        setSelectedAgreementId(a.id)
                        setShowAgreementDropdown(false)
                        setAgreementSearch('')
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-sm text-neutral-900 font-mono">{a.number}</span>
                        <span className="text-xs text-neutral-500">{a.currency}</span>
                      </div>
                      <div className="text-xs text-neutral-500 mt-0.5">{a.vendor_name} · {a.title}</div>
                      <div className="text-xs text-neutral-400 mt-0.5">
                        Valid {formatDate(a.valid_from)} – {formatDate(a.valid_to)}
                      </div>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Agreement summary card */}
        {selectedAgreement && (
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <MetaRow label="Vendor" value={selectedAgreement.vendor_name} />
            <MetaRow label="Currency" value={selectedAgreement.currency} />
            <MetaRow label="Valid" value={`${formatDate(selectedAgreement.valid_from)} – ${formatDate(selectedAgreement.valid_to)}`} />
            {selectedAgreement.not_to_exceed && (
              <MetaRow
                label="Not to Exceed"
                value={formatAmount(Number(selectedAgreement.not_to_exceed), selectedAgreement.currency)}
                mono
              />
            )}
          </div>
        )}
      </div>

      {selectedAgreement && (
        <>
          {/* Receipt Type */}
          <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Receipt Type</h2>
            <div className="flex gap-3">
              {TYPE_OPTIONS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setReceiptType(t)}
                  className={cn(
                    'flex-1 rounded-lg border px-4 py-2.5 text-sm font-medium transition-colors',
                    receiptType === t
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-neutral-300 text-neutral-600 hover:border-neutral-400'
                  )}
                >
                  {RECEIPT_TYPE_LABELS[t]}
                </button>
              ))}
            </div>
          </div>

          {/* Photo → OCR → editable fields → submit → attach, reused as-is
              (Task 5's ReceiptEntryForm) — only the agreement/type it records
              against are now supplied by this page instead of being fixed to
              whatever agreement the form used to be embedded in. */}
          <div className="rounded-xl border border-neutral-200 bg-white p-6">
            <ReceiptEntryForm agreementId={selectedAgreement.id} receiptType={receiptType} />
          </div>
        </>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-2 pb-6">
        <Link to={cancelHref}>
          <Button variant="secondary">Cancel</Button>
        </Link>
      </div>
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-neutral-500 min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium', mono && 'font-mono')}>{value}</span>
    </div>
  )
}
