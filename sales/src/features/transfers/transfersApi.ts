import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

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
}
export interface TransferIn { from_warehouse_id: number; to_warehouse_id: number; notes?: string | null; lines: { item_code: string; qty_ea: number }[] }

export const transfersClient = createResourceClient<TransferRead, never>(api, '/transfers')
export const fetchTransfer = (id: number) => api.get<TransferDetail>(`/transfers/${id}`)
export const createTransfer = (body: TransferIn) => api.post<TransferDetail>('/transfers', body)
export const updateTransfer = (id: number, version: number, body: TransferIn) => api.put<TransferDetail>(`/transfers/${id}?version=${version}`, body)
export const readyTransfer = (id: number, version: number) => api.post<TransferDetail>(`/transfers/${id}/ready?version=${version}`)
export const cancelTransfer = (id: number, version: number, reason: string) => api.post<TransferDetail>(`/transfers/${id}/cancel?version=${version}`, { reason })
export const resendTransfer = (id: number, version: number) => api.post<TransferDetail>(`/transfers/${id}/resend?version=${version}`)

export const printTransfer = (id: number, number: string) => api.download(`/transfers/${id}/print.pdf`, `TO-${number}.pdf`)
