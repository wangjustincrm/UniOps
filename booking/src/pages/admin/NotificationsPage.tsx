/**
 * NotificationsPage — /admin/notifications
 *
 * Filters: status, notif_type
 * Table: created_at, type, recipients (joined + truncated), status badge,
 *        retry_count, error (truncated w/ title), sent_at, Resend button
 *
 * Resend: disabled while in flight; row refreshes after success.
 * Banner warns that resend duplicates emails for already-sent logs.
 */
import { useState } from 'react'
import {
  Loader2,
  AlertCircle,
  X as XIcon,
  Info,
  RefreshCw,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  useAdminNotifications,
  useAdminResendNotification,
} from '@/services/api'
import type { NotificationLogOut } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-CA', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const STATUS_STYLE: Record<string, string> = {
  sent:     'bg-emerald-50 text-emerald-700 ring-emerald-200',
  pending:  'bg-amber-50  text-amber-700  ring-amber-200',
  failed:   'bg-red-50    text-red-600    ring-red-200',
  retrying: 'bg-amber-50  text-amber-700  ring-amber-200',
}

const TYPE_LABELS: Record<string, string> = {
  cancelled_occ: 'cancelled occurrence',
}

function NotifStatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? 'bg-neutral-100 text-neutral-500 ring-neutral-200'
  const label = status.charAt(0).toUpperCase() + status.slice(1)
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', style)}>
      {label}
    </span>
  )
}

function truncateRecipients(recipients: string[], maxLen = 40): { display: string; full: string } {
  const full = Array.isArray(recipients) ? recipients.join(', ') : String(recipients)
  return { display: full.length > maxLen ? full.slice(0, maxLen) + '…' : full, full }
}

// ── Row ───────────────────────────────────────────────────────────────────────

interface RowProps {
  log: NotificationLogOut
  resendingId: string | null
  onResend: (id: string) => void
}

function NotifRow({ log, resendingId, onResend }: RowProps) {
  const { display: recipDisp, full: recipFull } = truncateRecipients(log.recipients)
  const errorTrunc = log.error && log.error.length > 80 ? log.error.slice(0, 80) + '…' : log.error
  const isResending = resendingId === log.id
  const typeFmt = TYPE_LABELS[log.notif_type] ?? log.notif_type.replace(/_/g, ' ')

  const tdCls = 'px-3 py-3 text-sm'

  return (
    <tr className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 transition-colors">
      <td className={cn(tdCls, 'text-neutral-500 whitespace-nowrap text-xs')}>{fmtDateTime(log.created_at)}</td>
      <td className={cn(tdCls, 'text-neutral-700')}>{typeFmt}</td>
      <td className={cn(tdCls, 'text-neutral-600 max-w-[160px]')}>
        <span title={recipFull}>{recipDisp || '—'}</span>
      </td>
      <td className={tdCls}><NotifStatusBadge status={log.status} /></td>
      <td className={cn(tdCls, 'text-neutral-500 text-center')}>{log.retry_count}</td>
      <td className={cn(tdCls, 'text-red-600 max-w-[180px] text-xs')}>
        {errorTrunc ? <span title={log.error ?? undefined}>{errorTrunc}</span> : '—'}
      </td>
      <td className={cn(tdCls, 'text-neutral-500 whitespace-nowrap text-xs')}>
        {log.sent_at ? fmtDateTime(log.sent_at) : '—'}
      </td>
      <td className={tdCls}>
        <button
          type="button"
          disabled={isResending}
          onClick={() => onResend(log.id)}
          className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
        >
          {isResending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          Resend
        </button>
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50

export default function NotificationsPage() {
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [resendingId, setResendingId] = useState<string | null>(null)
  const [resendError, setResendError] = useState<string | null>(null)
  const [resendSuccess, setResendSuccess] = useState<string | null>(null)
  const [showWarning, setShowWarning] = useState(true)

  const filters = {
    status: statusFilter || undefined,
    notif_type: typeFilter || undefined,
    limit: PAGE_SIZE,
    offset,
  }

  const { data, isLoading, error, refetch } = useAdminNotifications(filters)
  const resendMut = useAdminResendNotification()

  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  function handleResend(id: string) {
    setResendingId(id)
    setResendError(null)
    resendMut.mutate(id, {
      onSuccess: () => {
        setResendingId(null)
        setResendSuccess('Notification queued for resend.')
        void refetch()
      },
      onError: (err) => {
        setResendingId(null)
        setResendError(err.message)
      },
    })
  }

  const thCls = 'px-3 py-3 text-xs font-semibold uppercase tracking-wider text-neutral-500 text-left'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-4">
        {/* Header */}
        <h1 className="text-2xl font-bold text-neutral-900">Notification Log</h1>

        {/* Resend warning banner */}
        {showWarning && (
          <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <Info className="h-4 w-4 shrink-0 mt-0.5" />
            <span className="flex-1">
              Resending a notification will send a new email to the original recipients, even if the
              previous attempt succeeded. Use this to recover from delivery failures, not to correct content.
            </span>
            <button type="button" onClick={() => setShowWarning(false)} className="text-amber-600 hover:text-amber-800">
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Banners */}
        {resendSuccess && (
          <div className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-700">
            <span>{resendSuccess}</span>
            <button type="button" onClick={() => setResendSuccess(null)}><XIcon className="h-4 w-4 text-emerald-500" /></button>
          </div>
        )}
        {resendError && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {resendError}
            <button type="button" onClick={() => setResendError(null)} className="ml-auto"><XIcon className="h-4 w-4" /></button>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-3">
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setOffset(0) }}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
          >
            <option value="">All Statuses</option>
            <option value="sent">Sent</option>
            <option value="pending">Pending</option>
            <option value="failed">Failed</option>
            <option value="retrying">Retrying</option>
          </select>
          <input
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setOffset(0) }}
            placeholder="Type filter…"
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40 w-44"
          />
        </div>

        {/* Content */}
        {isLoading && (
          <div className="flex items-center justify-center py-16 text-sm text-neutral-500">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading notifications…
          </div>
        )}
        {!isLoading && error && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load notifications.
          </div>
        )}
        {!isLoading && !error && (
          <>
            <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-neutral-200 bg-neutral-50">
                  <tr>
                    <th className={thCls}>Created</th>
                    <th className={thCls}>Type</th>
                    <th className={thCls}>Recipients</th>
                    <th className={thCls}>Status</th>
                    <th className={cn(thCls, 'text-center')}>Retries</th>
                    <th className={thCls}>Error</th>
                    <th className={thCls}>Sent At</th>
                    <th className={thCls}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-12 text-center text-sm text-neutral-400">
                        No notifications found.
                      </td>
                    </tr>
                  ) : (
                    (data?.items ?? []).map((log) => (
                      <NotifRow
                        key={log.id}
                        log={log}
                        resendingId={resendingId}
                        onResend={handleResend}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-between text-sm text-neutral-600">
                <span>{total} total</span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                    className="rounded-md border border-neutral-300 px-3 py-1 text-sm disabled:opacity-40 hover:bg-neutral-50"
                  >
                    Previous
                  </button>
                  <span>Page {currentPage} of {totalPages}</span>
                  <button
                    type="button"
                    disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset((o) => o + PAGE_SIZE)}
                    className="rounded-md border border-neutral-300 px-3 py-1 text-sm disabled:opacity-40 hover:bg-neutral-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
