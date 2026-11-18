import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useReplaceTab, Input, MenuItem } from '@uniops/shell'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useAgreements } from '@/hooks/useAgreements'
import { isAgreementAdmissible } from '@/lib/agreements'
import { ReceiptEntryForm } from '@/components/agreements/ReceiptEntryForm'
import { RECEIPT_TYPE_LABELS, type ReceiptType } from '@/services/agreementReceipts'
import type { ApiAgreement } from '@/services/agreement'

const TYPE_OPTIONS: ReceiptType[] = ['counter_slip', 'delivery', 'service']

// ─── Page ─────────────────────────────────────────────────────────────────────

// Skeleton (breadcrumb / cards / submit-cancel placement) copied from
// GrCreatePage.tsx per the Task 10 brief — receipts get their own create
// page the same way GR does, instead of being entered from inside the
// agreement's detail page.
export default function ReceiptCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
  const [searchParams] = useSearchParams()
  // No page/page_size in the filter → useAgreements pages through the full
  // result set itself (server default-truncates to 20 rows otherwise; see
  // hooks/useAgreements.ts), so this picker isn't silently missing agreements
  // past the first page.
  // EVERY agreement type, not just house_account. A receipt is a signed piece
  // of evidence — a counter slip, a delivery note, a service sign-off — and
  // nothing about the other two routes says such a document cannot exist for
  // them: a recurring cleaning contract still has monthly sign-offs, a
  // milestone build still has delivery notes. house_account is the type that
  // REQUIRES one at the payment gate (pa.py), which is not the same as being
  // the only type allowed to have one. The backend never had this restriction
  // — create_receipt checks the agreement's status, never its type.
  const { data: agreementsData, isLoading: agreementsLoading } = useAgreements()
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

  // True only while the agreement that arrived via ?agreement_id= genuinely
  // never resolves — NOT while the fetch is still in flight (agreementsLoading
  // covers that separately) and NOT once the user has touched the picker
  // (selectedAgreementId no longer equals the preselected value once they
  // clear/change it). Without this, someone arriving from the agreement
  // detail page's "New Receipt" button whose agreement turns out to be
  // ineligible (wrong type, or went non-admissible between the two page
  // loads) landed on a blank search box with zero explanation — indistinguishable
  // from the normal loading flash.
  const preselectFailed =
    !agreementsLoading && preselectedAgreementId !== '' && selectedAgreementId === preselectedAgreementId && !selectedAgreement

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
        <Link to="/receipts" className="text-fg-muted hover:text-fg-secondary transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-fg">New Agreement Receipt</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Record a counter slip, delivery note, or service sign-off against a house-account agreement
          </p>
        </div>
      </div>

      {/* Agreement Selection */}
      <div className="rounded-xl border border-line bg-surface p-6 flex flex-col gap-4">
        <h2 className="text-base font-semibold text-fg">Agreement</h2>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-fg-secondary">
            Select Agreement <span className="text-danger-fg">*</span>
          </label>
          {agreementsLoading ? (
            <div className="flex items-center gap-2 h-10 px-3 rounded-lg border border-line bg-surface-subtle text-sm text-fg-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading agreements…
            </div>
          ) : (
            <div className="relative">
              <Input
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
                className="w-full"
              />
              {selectedAgreement && (
                <Button variant="subtle" size="icon-xs"
                  type="button"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2"
                  onClick={() => { setSelectedAgreementId(''); setAgreementSearch(''); setShowAgreementDropdown(true) }}
                >
                  ×
                </Button>
              )}
              {showAgreementDropdown && !selectedAgreement && (
                <div className="absolute z-10 mt-1 w-full rounded-lg border border-line bg-surface shadow-lg max-h-64 overflow-y-auto">
                  {filteredAgreements.length === 0 ? (
                    <div className="px-4 py-3 text-sm text-fg-muted">
                      No eligible house-account agreements found (must be active, or expired within its grace period)
                    </div>
                  ) : (
                    filteredAgreements.map((a) => (
                      <MenuItem
                        key={a.id}
                        type="button"
                        className="px-4 py-3 border-b border-line-subtle last:border-0"
                        onClick={() => {
                          setSelectedAgreementId(a.id)
                          setShowAgreementDropdown(false)
                          setAgreementSearch('')
                        }}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-sm text-fg font-mono">{a.number}</span>
                          <span className="text-xs text-fg-muted">{a.currency}</span>
                        </div>
                        <div className="text-xs text-fg-muted mt-0.5">{a.vendor_name} · {a.title}</div>
                        <div className="text-xs text-fg-muted mt-0.5">
                          Valid {formatDate(a.valid_from)} – {formatDate(a.valid_to)}
                        </div>
                      </MenuItem>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
          {preselectFailed && (
            <p className="text-xs text-danger-fg">
              This agreement isn't available for receipts — it may not be a house-account agreement, or it's
              draft/cancelled/past its grace period. Search for a different one above.
            </p>
          )}
        </div>

        {/* Agreement summary card */}
        {selectedAgreement && (
          <div className="rounded-lg border border-line bg-surface-subtle p-4 grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
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
          <div className="rounded-xl border border-line bg-surface p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-fg">Receipt Type</h2>
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
                      : 'border-line-strong text-fg-secondary hover:border-fg-faint'
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
          <div className="rounded-xl border border-line bg-surface p-6">
            {/* replaceTab(cancelHref), not resetForm() alone — Task 10 fix
                round 1: a form that silently clears itself is indistinguishable
                from one that silently failed. Navigating to wherever Cancel
                would have gone (the source agreement, or the receipts list)
                is unambiguous confirmation AND lands somewhere the new receipt
                is actually visible. */}
            <ReceiptEntryForm
              agreementId={selectedAgreement.id}
              receiptType={receiptType}
              taxRate={selectedAgreement.tax_rate != null ? Number(selectedAgreement.tax_rate) : null}
              onSuccess={() => replaceTab(cancelHref)}
            />
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
      <span className="text-fg-muted min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-fg font-medium', mono && 'font-mono')}>{value}</span>
    </div>
  )
}
