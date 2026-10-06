import { AlertTriangle } from 'lucide-react'
import { formatDate } from '@/lib/utils'
import { dueWindowViolation, PA_INVOICE_DUE_WINDOW_DAYS } from './invoiceDueWindow'

// Shown live under an invoice picker as soon as the ticked invoices span more
// than the window — naming the two ends, so the operator knows which to untick
// rather than learning it from a rejected submit.
export function DueWindowNotice({ invoices }: { invoices: { internal_ref: string; due_date: string }[] }) {
  const v = dueWindowViolation(invoices)
  if (!v) return null
  return (
    <p role="alert" className="flex items-start gap-1.5 rounded-lg border border-danger-300 bg-danger-50 px-3 py-2 text-xs text-danger-700">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>
        Invoices on one payment must fall due within {PA_INVOICE_DUE_WINDOW_DAYS} days of each other.{' '}
        {v.earliest.internal_ref} is due {formatDate(v.earliest.due_date)} and {v.latest.internal_ref} is
        due {formatDate(v.latest.due_date)} ({v.spanDays} days apart) — untick one side and pay it on a
        separate payment application.
      </span>
    </p>
  )
}
