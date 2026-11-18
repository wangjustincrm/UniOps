import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { AlertTriangle, Download, ExternalLink, Paperclip, X } from 'lucide-react'

import {
  useCreditAttachments, useUploadCreditAttachment, useVendorCreditApplications,
} from '@/hooks/useVendorCredits'
import { formatAmount, formatDate } from '@/lib/utils'
import { FileInput, Button } from '@uniops/shell'
import {
  CREDIT_EVIDENCE_ACCEPT, creditAttachmentService, type CreditAttachment, type VendorCredit,
} from '@/services/vendorCredits'

/**
 * One credit, with its evidence. Until this existed nothing in EPMS showed a
 * credit's file at all — approval was on the table row alone. For a manual
 * credit the attached email IS the document, so it cannot be approved from
 * here while it has none (the table sends manual rows here rather than
 * offering an inline Approve).
 */

const SOURCE_LABEL: Record<VendorCredit['source'], string> = {
  upload: 'Credit note upload',
  manual: 'Manual — no credit note issued',
  qbo_import: 'Imported from QuickBooks',
}

// Browsers can show these inline; a saved .msg/.eml can only be downloaded.
const opensInBrowser = (ct: string) => ct === 'application/pdf' || ct.startsWith('image/') || ct === 'text/plain'

interface Props {
  credit: VendorCredit
  canManage: boolean
  reviewPending: boolean
  approveError: string | null
  onClose: () => void
  onApprove: () => void
  onReject: () => void
  onVoid: () => void
}

export function CreditDetailDrawer({
  credit, canManage, reviewPending, approveError, onClose, onApprove, onReject, onVoid,
}: Props) {
  const atts = useCreditAttachments(credit.id)
  // A credit still in review cannot have been used yet — nothing to fetch.
  const usage = useVendorCreditApplications(credit.status === 'pending_review' ? null : credit.id)
  const upload = useUploadCreditAttachment(credit.id)
  const [fileError, setFileError] = useState<string | null>(null)

  const files = atts.data ?? []
  const needsEvidence = credit.source === 'manual' && atts.isSuccess && files.length === 0
  const money = (v: string) => formatAmount(Number(v), credit.currency)

  const openFile = async (a: CreditAttachment, download: boolean) => {
    setFileError(null)
    // Open the tab now, inside the click — after the await a popup blocker
    // no longer treats it as user-initiated.
    const tab = download ? null : window.open('', '_blank')
    try {
      const url = URL.createObjectURL(await creditAttachmentService.blob(a.id))
      if (tab) {
        tab.location.href = url
      } else {
        const link = document.createElement('a')
        link.href = url; link.download = a.file_name; link.click()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (e) {
      tab?.close()
      setFileError(e instanceof Error ? e.message : 'Could not open file')
    }
  }

  const row = (label: string, value: ReactNode) => (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="text-right text-fg">{value}</span>
    </div>
  )

  return createPortal(
    <div className="fixed inset-0 z-40 flex justify-end bg-inverse/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-md flex-col bg-surface shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-line-subtle px-5 py-4">
          <div>
            <h2 className="font-mono text-sm font-semibold text-fg">{credit.credit_number}</h2>
            <p className="mt-0.5 text-xs text-fg-muted">{SOURCE_LABEL[credit.source]}</p>
          </div>
          <Button variant="subtle" size="icon-xs" onClick={onClose} aria-label="Close">
            <X className="h-5 w-5" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div className="divide-y divide-line-subtle">
            {row('Vendor', credit.vendor_name)}
            {row('Vendor reference #', <span className="font-mono">{credit.vendor_credit_number}</span>)}
            {row('Credit date', formatDate(credit.credit_date))}
            {row('Amount', <span className="font-mono">{money(credit.amount)}</span>)}
            {row('Tax', <span className="font-mono">{money(credit.tax_amount)}</span>)}
            {row('Total', <span className="font-mono font-semibold">{money(credit.total_amount)}</span>)}
            {row('Applied', <span className="font-mono">{money(credit.applied_amount)}</span>)}
            {/* Voided: nothing left to apply (ISS-0189); display only. */}
            {row('Remaining', <span className="font-mono font-semibold">{money(credit.status === 'void' ? '0' : credit.remaining_amount)}</span>)}
            {row('PO', credit.po_number ?? '—')}
            {row('Recorded by', `${credit.uploaded_by_name ?? '—'} · ${formatDate(credit.uploaded_at)}`)}
            {credit.reviewed_at && row('Reviewed by', `${credit.reviewed_by_name ?? '—'} · ${formatDate(credit.reviewed_at)}`)}
          </div>

          {credit.notes && (
            <div className="mt-4">
              <h3 className="text-xs font-semibold uppercase text-fg-muted">
                {credit.source === 'manual' ? 'Basis' : 'Notes'}
              </h3>
              <p className="mt-1 whitespace-pre-wrap text-sm text-fg">{credit.notes}</p>
            </div>
          )}
          {credit.review_note && (
            <div className="mt-4">
              <h3 className="text-xs font-semibold uppercase text-fg-muted">Review note</h3>
              <p className="mt-1 whitespace-pre-wrap text-sm text-fg">{credit.review_note}</p>
            </div>
          )}

          {credit.status !== 'pending_review' && (
            <div className="mt-5">
              <h3 className="text-xs font-semibold uppercase text-fg-muted">Usage</h3>
              {usage.isLoading ? (
                <p className="mt-2 text-xs text-fg-muted">Loading…</p>
              ) : usage.isError ? (
                <p className="mt-2 text-xs text-fg-muted">
                  {(usage.error as Error & { status?: number }).status === 403
                    ? 'Usage history is visible to finance roles only.'
                    : (usage.error as Error).message}
                </p>
              ) : !usage.data || usage.data.items.length === 0 ? (
                <p className="mt-2 text-xs text-fg-muted">Not applied to any payment yet.</p>
              ) : (
                <>
                  <ul className="mt-2 flex flex-col gap-1.5">
                    {usage.data.items.map((a) => (
                      <li key={a.id} className="rounded bg-surface-subtle px-2.5 py-2 text-xs">
                        <div className="flex items-center justify-between gap-3">
                          <Link to={`/pa/${a.doc_id}`} onClick={onClose}
                                className="font-mono font-medium text-primary-700 hover:underline">
                            {a.doc_number ?? 'Payment application'}
                          </Link>
                          <span className="font-mono font-semibold text-fg">−{money(a.applied_amount)}</span>
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-fg-muted">
                          <span>{formatDate(a.payment_date ?? a.applied_at)}</span>
                          {a.batch_number && <span className="font-mono">{a.batch_number}</span>}
                          {a.cash_paid != null && (
                            <span title="Cash paid to the vendor on this payment, after the credit">
                              Paid <span className="font-mono">{money(a.cash_paid)}</span>
                            </span>
                          )}
                          {a.applied_by_name && <span>by {a.applied_by_name}</span>}
                          {a.payment_status && a.payment_status !== 'completed' && (
                            <span className="text-warning-fg">{a.payment_status}</span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 flex justify-between border-t border-line-subtle pt-2 text-xs">
                    <span className="text-fg-muted">
                      {usage.data.total} payment{usage.data.total === 1 ? '' : 's'}
                    </span>
                    <span className="font-mono font-semibold text-fg">{money(usage.data.total_applied)}</span>
                  </div>
                </>
              )}
            </div>
          )}

          <div className="mt-5">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase text-fg-muted">Evidence</h3>
              {credit.status !== 'void' && (
                <label className="flex cursor-pointer items-center gap-1 text-xs font-medium text-primary-700 hover:text-primary-800">
                  <Paperclip className="h-3.5 w-3.5" />
                  {upload.isPending ? 'Attaching…' : 'Add file'}
                  <FileInput accept={CREDIT_EVIDENCE_ACCEPT} className="hidden" disabled={upload.isPending}
                         onChange={(e) => {
                           const f = e.target.files?.[0]
                           e.target.value = ''
                           if (f) { setFileError(null); upload.mutate(f, { onError: (err) => setFileError(err.message) }) }
                         }} />
                </label>
              )}
            </div>
            {atts.isLoading ? (
              <p className="mt-2 text-xs text-fg-muted">Loading…</p>
            ) : atts.isError ? (
              <p className="mt-2 text-xs text-danger-fg">{(atts.error as Error).message}</p>
            ) : files.length === 0 ? (
              <p className="mt-2 text-xs text-fg-muted">No files attached.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1">
                {files.map((a) => (
                  <li key={a.id} className="flex items-center justify-between rounded bg-surface-subtle px-2 py-1.5 text-xs">
                    <span className="truncate" title={a.file_name}>{a.file_name}</span>
                    <span className="flex shrink-0 gap-2">
                      {opensInBrowser(a.content_type) && (
                        <Button variant="subtle" size="icon-xs" onClick={() => void openFile(a, false)} aria-label={`Open ${a.file_name}`}
                               >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </Button>
                      )}
                      <Button variant="subtle" size="icon-xs" onClick={() => void openFile(a, true)} aria-label={`Download ${a.file_name}`}
                             >
                        <Download className="h-3.5 w-3.5" />
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {fileError && <p className="mt-2 text-xs text-danger-fg">{fileError}</p>}
          </div>
        </div>

        {canManage && (credit.status === 'pending_review'
          || (credit.status === 'available' && Number(credit.applied_amount) === 0)) && (
          <div className="border-t border-line-subtle px-5 py-3">
            {needsEvidence && (
              <p className="mb-2 flex items-center gap-1.5 text-xs text-warning-fg">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                A manual credit needs the vendor's email attached before it can be approved.
              </p>
            )}
            {approveError && (
              <p className="mb-2 flex items-center gap-1.5 text-xs text-danger-fg">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {approveError}
              </p>
            )}
            <div className="flex justify-end gap-2">
              {credit.status === 'pending_review' ? (
                <>
                  <Button variant="danger-outline" size="sm" onClick={onReject} disabled={reviewPending}
                         >
                    Reject
                  </Button>
                  <Button variant="success" size="sm" onClick={onApprove}
                          disabled={reviewPending || needsEvidence || (credit.source === 'manual' && !atts.isSuccess)}
                         >
                    Approve
                  </Button>
                </>
              ) : (
                <Button variant="secondary" size="sm" onClick={onVoid} disabled={reviewPending}
                       >
                  Void
                </Button>
              )}
            </div>
          </div>
        )}
      </aside>
    </div>,
    document.body,
  )
}
