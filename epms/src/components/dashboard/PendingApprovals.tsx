import { formatCAD } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { Link } from 'react-router-dom'
import { CheckCircle2, XCircle, RotateCcw } from 'lucide-react'
import type { DocumentStatus } from '@/types'

export interface ApprovalItem {
  id: string
  docType: 'PR' | 'PO' | 'PA'
  number: string
  title: string
  requester?: string
  department?: string
  amount: number
  status: DocumentStatus
  submittedDaysAgo: number
  extraBadge?: string
  href: string
}

interface PendingApprovalsProps {
  items: ApprovalItem[]
  onApprove?: (id: string) => void
  onReject?: (id: string) => void
  onReturn?: (id: string) => void
}

export function PendingApprovals({
  items,
  onApprove,
  onReject,
  onReturn,
}: PendingApprovalsProps) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-neutral-900">
          Pending Approvals
          <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning-500 px-1 text-[10px] font-bold text-white">
            {items.length}
          </span>
        </h2>
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          Sort: Oldest first
        </div>
      </div>

      {items.length === 0 && (
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] py-10 text-center">
          <p className="text-sm text-neutral-400">No pending approvals</p>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {items.map((item) => (
          <div
            key={item.id}
            className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-4"
          >
            <div className="flex items-start justify-between gap-3 mb-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <Link
                    to={item.href}
                    className="text-sm font-semibold text-neutral-900 hover:text-primary-600"
                  >
                    {item.number}
                  </Link>
                  <StatusBadge status={item.status} />
                  {item.extraBadge && (
                    <span className="rounded-full bg-success-50 px-2 py-0.5 text-xs text-success-700 font-medium">
                      {item.extraBadge}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-sm text-neutral-700">{item.title}</p>
                {(item.requester || item.department) && (
                  <p className="mt-0.5 text-xs text-neutral-500">
                    {[item.requester, item.department].filter(Boolean).join(' · ')}
                  </p>
                )}
              </div>
              <div className="shrink-0 text-right">
                <p className="amount text-sm font-semibold text-neutral-900">
                  {formatCAD(item.amount)}
                </p>
                <p className="text-xs text-neutral-400">
                  {item.submittedDaysAgo === 0
                    ? 'Today'
                    : `${item.submittedDaysAgo} day${item.submittedDaysAgo > 1 ? 's' : ''} ago`}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 pt-2 border-t border-neutral-100">
              <Button
                variant="success-outline"
                size="sm"
                onClick={() => onApprove?.(item.id)}
                className="flex-1 sm:flex-none"
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                Approve
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => onReturn?.(item.id)}
                className="flex-1 sm:flex-none"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Return
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => onReject?.(item.id)}
                className="flex-1 sm:flex-none"
              >
                <XCircle className="h-3.5 w-3.5" />
                Reject
              </Button>
              <Link
                to={item.href}
                className="ml-auto text-xs text-primary-600 hover:underline whitespace-nowrap"
              >
                Full review →
              </Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
