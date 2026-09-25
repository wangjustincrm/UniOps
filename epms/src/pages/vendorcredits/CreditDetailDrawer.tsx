import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Download, ExternalLink, Paperclip, X } from 'lucide-react'

import { useCreditAttachments, useUploadCreditAttachment } from '@/hooks/useVendorCredits'
import { formatAmount, formatDate } from '@/lib/utils'
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
      <span className="text-neutral-500">{label}</span>
      <span className="text-right text-neutral-900">{value}</span>
    </div>
  )

  return createPortal(
    <div className="fixed inset-0 z-40 flex justify-end bg-neutral-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-md flex-col bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-neutral-100 px-5 py-4">
          <div>
            <h2 className="font-mono text-sm font-semibold text-neutral-900">{credit.credit_number}</h2>
            <p className="mt-0.5 text-xs text-neutral-500">{SOURCE_LABEL[credit.source]}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-neutral-400 hover:text-neutral-600">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div className="divide-y divide-neutral-100">
            {row('Vendor', credit.vendor_name)}
            {row('Vendor reference #', <span className="font-mono">{credit.vendor_credit_number}</span>)}
            {row('Credit date', formatDate(credit.credit_date))}
            {row('Amount', <span className="font-mono">{money(credit.amount)}</span>)}
            {row('Tax', <span className="font-mono">{money(credit.tax_amount)}</span>)}
            {row('Total', <span className="font-mono font-semibold">{money(credit.total_amount)}</span>)}
            {row('Applied', <span className="font-mono">{money(credit.applied_amount)}</span>)}
            {row('Remaining', <span className="font-mono font-semibold">{money(credit.remaining_amount)}</span>)}
            {row('PO', credit.po_number ?? '—')}
            {row('Recorded by', `${credit.uploaded_by_name ?? '—'} · ${formatDate(credit.uploaded_at)}`)}
            {credit.reviewed_at && row('Reviewed by', `${credit.reviewed_by_name ?? '—'} · ${formatDate(credit.reviewed_at)}`)}
          </div>

          {credit.notes && (
            <div className="mt-4">
              <h3 className="text-xs font-semibold uppercase text-neutral-500">
                {credit.source === 'manual' ? 'Basis' : 'Notes'}
              </h3>
              <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-800">{credit.notes}</p>
            </div>
          )}
          {credit.review_note && (
            <div className="mt-4">
              <h3 className="text-xs font-semibold uppercase text-neutral-500">Review note</h3>
              <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-800">{credit.review_note}</p>
            </div>
          )}

          <div className="mt-5">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase text-neutral-500">Evidence</h3>
              {credit.status !== 'void' && (
                <label className="flex cursor-pointer items-center gap-1 text-xs font-medium text-primary-700 hover:text-primary-800">
                  <Paperclip className="h-3.5 w-3.5" />
                  {upload.isPending ? 'Attaching…' : 'Add file'}
                  <input type="file" accept={CREDIT_EVIDENCE_ACCEPT} className="hidden" disabled={upload.isPending}
                         onChange={(e) => {
                           const f = e.target.files?.[0]
                           e.target.value = ''
                           if (f) { setFileError(null); upload.mutate(f, { onError: (err) => setFileError(err.message) }) }
                         }} />
                </label>
              )}
            </div>
            {atts.isLoading ? (
              <p className="mt-2 text-xs text-neutral-400">Loading…</p>
            ) : atts.isError ? (
              <p className="mt-2 text-xs text-danger-600">{(atts.error as Error).message}</p>
            ) : files.length === 0 ? (
              <p className="mt-2 text-xs text-neutral-400">No files attached.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1">
                {files.map((a) => (
                  <li key={a.id} className="flex items-center justify-between rounded bg-neutral-50 px-2 py-1.5 text-xs">
                    <span className="truncate" title={a.file_name}>{a.file_name}</span>
                    <span className="flex shrink-0 gap-2">
                      {opensInBrowser(a.content_type) && (
                        <button onClick={() => void openFile(a, false)} aria-label={`Open ${a.file_name}`}
                                className="text-neutral-500 hover:text-primary-700">
                          <ExternalLink className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button onClick={() => void openFile(a, true)} aria-label={`Download ${a.file_name}`}
                              className="text-neutral-500 hover:text-primary-700">
                        <Download className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {fileError && <p className="mt-2 text-xs text-danger-600">{fileError}</p>}
          </div>
        </div>

        {canManage && (credit.status === 'pending_review'
          || (credit.status === 'available' && Number(credit.applied_amount) === 0)) && (
          <div className="border-t border-neutral-100 px-5 py-3">
            {needsEvidence && (
              <p className="mb-2 flex items-center gap-1.5 text-xs text-warning-700">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                A manual credit needs the vendor's email attached before it can be approved.
              </p>
            )}
            {approveError && (
              <p className="mb-2 flex items-center gap-1.5 text-xs text-danger-700">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {approveError}
              </p>
            )}
            <div className="flex justify-end gap-2">
              {credit.status === 'pending_review' ? (
                <>
                  <button onClick={onReject} disabled={reviewPending}
                          className="rounded border border-danger-300 px-3 py-1.5 text-sm font-medium text-danger-700 disabled:opacity-50">
                    Reject
                  </button>
                  <button onClick={onApprove}
                          disabled={reviewPending || needsEvidence || (credit.source === 'manual' && !atts.isSuccess)}
                          className="rounded bg-success-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
                    Approve
                  </button>
                </>
              ) : (
                <button onClick={onVoid} disabled={reviewPending}
                        className="rounded border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 disabled:opacity-50">
                  Void
                </button>
              )}
            </div>
          </div>
        )}
      </aside>
    </div>,
    document.body,
  )
}
