import { registerStatusTones, statusTone } from '@/platform-ui'

// The one place a status of this module gets its tone: lists and record tiles both read it.
// Order stages have their own colours (features/orders/orderStatus.ts).
registerStatusTones({
  Pending: 'info', Processing: 'info', Processed: 'success', Failed: 'danger', Skipped: 'neutral', Duplicate: 'neutral',
  RUNNING: 'info', SUCCEEDED: 'success', PARTIAL: 'warning', FAILED: 'danger',
  ERROR: 'danger', WARNING: 'warning', INFO: 'neutral',
  'Ready To Send': 'info', Sending: 'info', Sent: 'success', Suppressed: 'warning', Replaced: 'neutral', Cancelled: 'neutral',
  Waiting: 'info', Accepted: 'success', 'Accepted by 945': 'success', Rejected: 'danger', Timeout: 'warning',
  'Not Required': 'neutral', Draft: 'neutral', Review: 'warning', 'Payment Hold': 'warning', 'Archive Suppressed': 'warning',
  Shipped: 'success', 'On Hold': 'warning', Active: 'success', Inactive: 'neutral', Completed: 'success',
  Blocked: 'warning', Discrepancy: 'danger', 'Partially Received': 'warning', Received: 'success',
  Match: 'success', 'Expected difference': 'success', Differs: 'warning', Unreadable: 'warning',
  'No Sales order': 'warning', 'Sales only': 'warning', 'No reference': 'warning',
})

/** A record tile's tone for a status: the same as its badge in the lists. */
export function factTone(status: string | null | undefined): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  const tone = statusTone(status)
  return tone === 'dark' ? 'neutral' : tone
}
