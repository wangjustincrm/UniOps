import { api } from '@/lib/api'
import type { FileSource } from '@/platform-ui'

/** What an order and a transfer share (user 2026-11-13): their files, attachments and activity. */
export type RecordType = 'order' | 'transfer'

export interface RecordDocument {
  id: number
  kind: string
  direction: 'IN' | 'OUT'
  remote_name: string | null
  created_at: string
  file_status: string | null
  ack_status: string | null
  st02: string | null
  sent_at: string | null
  error: string | null
  ack_detail: string | null
  size?: number | null
  created_by_name?: string | null
  /** An attachment's note. */
  note?: string | null
}

export interface TimelineEntry {
  at: string
  user_name: string
  entity: string
  action: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  reason: string | null
}

const base = (t: RecordType, id: number) => `/attachments/${t}/${id}`
export const listAttachments = (t: RecordType, id: number) => api.get<RecordDocument[]>(base(t, id))
export function attachFile(t: RecordType, id: number, file: File, note: string | null) {
  const form = new FormData()
  form.append('file', file)
  if (note) form.append('note', note)
  return api.upload<RecordDocument>(base(t, id), form)
}
export const removeAttachment = (t: RecordType, id: number, documentId: number, reason: string) =>
  api.post<void>(`${base(t, id)}/${documentId}/remove`, { reason })

/** A stored file (received, sent or attached), looked at in FileViewer. */
export const recordFile = (d: RecordDocument): FileSource => ({
  key: `document-${d.id}`, title: `${d.kind === 'ATTACHMENT' ? 'Attachment' : d.kind}${d.direction === 'OUT' ? ' (sent)' : ''}: ${d.remote_name ?? d.id}`,
  load: () => api.file(`/documents/${d.id}/raw`, d.remote_name ?? `document-${d.id}`),
})
export const downloadRecordFile = (d: RecordDocument) => api.download(`/documents/${d.id}/raw`, d.remote_name ?? `document-${d.id}`)

/** Who may attach to which record: its own change permission. */
export const ATTACH_PERMISSION: Record<RecordType, string> = { order: 'sales.order.operate', transfer: 'sales.transfer.manage' }
