import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

export interface AttachmentMeta {
  id: string
  filename: string
  content_type: string
  file_size: number
  created_at: string
}

const base = () => (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
const token = () => useAuthStore.getState().token

// Pickup-slip attachments are scoped by BOTH agreement_id and slip_id on the
// backend (epms-api/app/api/v1/agreement_slip_attachments.py) — every route
// re-validates the slip belongs to the agreement before touching an
// attachment. Task 3 fixed an IDOR here once already; both ids must always be
// passed, never just slip_id.
export const agreementSlipAttachmentService = {
  list: (agreementId: string, slipId: string) =>
    api.get<AttachmentMeta[]>(`/agreements/${agreementId}/slips/${slipId}/attachments`),

  upload: async (agreementId: string, slipId: string, file: File): Promise<AttachmentMeta> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${base()}/agreements/${agreementId}/slips/${slipId}/attachments`, {
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
  download: (agreementId: string, slipId: string, attId: string, filename: string) => {
    fetch(`${base()}/agreements/${agreementId}/slips/${slipId}/attachments/${attId}/download`, {
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    }).then(async (res) => {
      if (!res.ok) return
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    })
  },

  delete: async (agreementId: string, slipId: string, attId: string): Promise<void> => {
    const res = await fetch(`${base()}/agreements/${agreementId}/slips/${slipId}/attachments/${attId}`, {
      method: 'DELETE',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
  },
}
