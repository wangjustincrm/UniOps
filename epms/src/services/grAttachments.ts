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

export const grAttachmentService = {
  list: (grId: string) =>
    api.get<AttachmentMeta[]>(`/gr/${grId}/attachments`),

  regeneratePdf: (grId: string) =>
    api.post<AttachmentMeta>(`/gr/${grId}/attachments/regenerate-pdf`),

  upload: async (grId: string, file: File): Promise<AttachmentMeta> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${base()}/gr/${grId}/attachments`, {
      method: 'POST',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      body: form,
    })
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
    return res.json()
  },

  download: (grId: string, attId: string, filename: string) => {
    fetch(`${base()}/gr/${grId}/attachments/${attId}/download`, {
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

  delete: async (grId: string, attId: string): Promise<void> => {
    const res = await fetch(`${base()}/gr/${grId}/attachments/${attId}`, {
      method: 'DELETE',
      headers: token() ? { Authorization: `Bearer ${token()}` } : {},
    })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
  },
}
