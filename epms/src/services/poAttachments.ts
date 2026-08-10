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

export const poAttachmentService = {
  list: (poId: string) =>
    api.get<AttachmentMeta[]>(`/po/${poId}/attachments`),

  regeneratePdf: (poId: string) =>
    api.post<AttachmentMeta>(`/po/${poId}/attachments/regenerate-pdf`),

  upload: async (poId: string, file: File): Promise<AttachmentMeta> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${base()}/po/${poId}/attachments`, {
      method: 'POST',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      body: form,
    })
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
    return res.json()
  },

  download: async (poId: string, attId: string, filename: string): Promise<void> => {
    const res = await fetch(`${base()}/po/${poId}/attachments/${attId}/download`, {
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

  delete: async (poId: string, attId: string): Promise<void> => {
    const res = await fetch(`${base()}/po/${poId}/attachments/${attId}`, {
      method: 'DELETE',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
  },
}
