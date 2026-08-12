import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

// The one query key for a receipt's attachment list, shared by every reader
// and every writer of it (ReceiptTable's per-row cell, ReceiptDetailPage's
// photo section, ReceiptEntryForm's post-create upload, and the upload/delete
// mutations in hooks/useAgreementReceipts.ts).
//
// A DELIBERATELY SEPARATE top-level namespace, NOT ['agreements', agreementId,
// 'receipts', ...]: react-query's invalidateQueries prefix-matches, so if this
// lived under that branch, the receipt mutations' invalidation (see
// hooks/useAgreementReceipts.ts) would sweep every row's attachment query on
// every Void/Approve/Reject click, N requests at a time.
//
// Lives HERE, next to the calls it keys, rather than in a component: hooks/
// useAgreementReceipts.ts needs it for the upload/delete mutations, and
// components/agreements/ReceiptTable.tsx (where it used to live) already
// imports from that hooks module — importing back the other way would close an
// import cycle.
export function receiptAttachmentsQueryKey(agreementId: string, receiptId: string) {
  return ['agreement-receipt-attachments', agreementId, receiptId] as const
}

export interface AttachmentMeta {
  id: string
  filename: string
  content_type: string
  file_size: number
  created_at: string
}

const base = () => (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
const token = () => useAuthStore.getState().token

// Pickup-receipt attachments are scoped by BOTH agreement_id and receipt_id on the
// backend (epms-api/app/api/v1/agreement_receipt_attachments.py) — every route
// re-validates the receipt belongs to the agreement before touching an
// attachment. Task 3 fixed an IDOR here once already; both ids must always be
// passed, never just receipt_id.
export const agreementReceiptAttachmentService = {
  list: (agreementId: string, receiptId: string) =>
    api.get<AttachmentMeta[]>(`/agreements/${agreementId}/receipts/${receiptId}/attachments`),

  upload: async (agreementId: string, receiptId: string, file: File): Promise<AttachmentMeta> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${base()}/agreements/${agreementId}/receipts/${receiptId}/attachments`, {
      method: 'POST',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      body: form,
    })
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
    return res.json()
  },

  // Must fetch with the auth token and hand the blob to a synthetic <a> —
  // never a bare <a href>. An unauthenticated request to the download route
  // gets 401'd and nginx's SPA fallback silently redirects it to the app
  // shell, which reads to the user as "clicking download bounces to the
  // homepage". Mirrors services/prAttachments.ts.
  //
  // Returns a promise that REJECTS on a failed download (Task 12). It used to
  // `return` silently on !res.ok, so a 403/404/500 produced no file, no error,
  // and no message — indistinguishable from a click that didn't register.
  // Every caller must therefore attach a .catch that tells the user.
  download: async (agreementId: string, receiptId: string, attId: string, filename: string): Promise<void> => {
    const res = await fetch(`${base()}/agreements/${agreementId}/receipts/${receiptId}/attachments/${attId}/download`, {
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    })
    if (!res.ok) throw new Error(`Download failed: ${res.status}`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  delete: async (agreementId: string, receiptId: string, attId: string): Promise<void> => {
    const res = await fetch(`${base()}/agreements/${agreementId}/receipts/${receiptId}/attachments/${attId}`, {
      method: 'DELETE',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
  },
}
