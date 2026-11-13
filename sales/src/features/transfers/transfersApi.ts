import { createResourceClient, type FileSource } from '@/platform-ui'
import { api } from '@/lib/api'
import type { RecordDocument, TimelineEntry } from '@/features/common/recordFiles'

// Mirrors sales-api app/schemas/transfers.py (hand-written, see partnersApi.ts).
export interface TransferRead {
  id: number
  transfer_number: string
  from_warehouse_id: number
  to_warehouse_id: number
  status: string
  notes: string | null
  document_id: number | null
  error: string | null
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  from_warehouse: string | null
  to_warehouse: string | null
  /** The list's facts (as the orders' list). */
  sent_ea: number
  received_ea: number
  items: string | null
  receipt_date: string | null
  attachments: number
}
export interface TransferLineRead { id: number; line_no: number; item_code: string; qty_ea: number; received_ea: number; line_status: string }
export interface ReceiptRowRead { position: number; lx_number: string | null; item_code: string | null; upc: string | null; lot: string | null;
  description: string | null; qty_ea: number; transfer_line_no: number | null }
export interface ReceiptRead { id: number; document_id: number; receipt_date: string | null; total_ea: number | null; applied: boolean;
  shadow: boolean; outcome: string | null; discrepancies: string[] | null; created_at: string; document_name: string | null; rows: ReceiptRowRead[] }
export interface TransferDetail extends TransferRead {
  lines: TransferLineRead[]
  receipts: ReceiptRead[]
  ack_status: string | null
  file_status: string | null
  control: string | null
  status_before_hold: string | null
  /** Received, sent and attached files. */
  documents: RecordDocument[]
  timeline: TimelineEntry[]
}
export interface TransferIn { from_warehouse_id: number; to_warehouse_id: number; notes?: string | null; lines: { item_code: string; qty_ea: number }[] }

export const transfersClient = createResourceClient<TransferRead, never>(api, '/transfers')
export const fetchTransfer = (id: number) => api.get<TransferDetail>(`/transfers/${id}`)
export const createTransfer = (body: TransferIn) => api.post<TransferDetail>('/transfers', body)
export const updateTransfer = (id: number, version: number, body: TransferIn) => api.put<TransferDetail>(`/transfers/${id}?version=${version}`, body)
export const readyTransfer = (id: number, version: number) => api.post<TransferDetail>(`/transfers/${id}/ready?version=${version}`)
export const releaseTransfer = (id: number, version: number, reason: string) => api.post<TransferDetail>(`/transfers/${id}/release?version=${version}`, { reason })
export const cancelTransfer = (id: number, version: number, reason: string) => api.post<TransferDetail>(`/transfers/${id}/cancel?version=${version}`, { reason })
export const resendTransfer = (id: number, version: number) => api.post<TransferDetail>(`/transfers/${id}/resend?version=${version}`)

export const transferPdf = (id: number, number: string): FileSource => ({
  key: `transfer-${id}`, title: `Transfer order ${number}`, load: () => api.file(`/transfers/${id}/print.pdf`, `TO-${number}.pdf`) })
