import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, UserCheck, Users } from 'lucide-react'
import { useActiveVisits, useBatchCheckOut } from '@/services/api'
import { AccessAreaBadge } from '@/components/StatusBadge'
import { formatDateTime, timeAgo } from '@/lib/utils'

function getRole(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.user?.role) return state.user.role
    }
    return null
  } catch { return null }
}

export default function ActiveVisitsPage() {
  const { data, isLoading } = useActiveVisits()
  const items = data ?? []
  const isAdmin = getRole() === 'system_admin'

  const batch = useBatchCheckOut()
  const [confirming, setConfirming] = useState(false)

  const onBatchClose = () => {
    if (!confirm(`Close all ${items.length} on-site visitors now? They will all be marked checked out.`)) {
      return
    }
    setConfirming(true)
    batch.mutate(undefined, {
      onSettled: () => setConfirming(false),
    })
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <UserCheck className="h-6 w-6 text-primary-600" />
          <h1 className="text-2xl font-bold text-neutral-900">On-Site Now</h1>
          {!isLoading && (
            <span className="rounded-full bg-success-50 px-2.5 py-0.5 text-xs font-semibold text-success-600 ring-1 ring-inset ring-emerald-200">
              {items.length}
            </span>
          )}
        </div>

        {isAdmin && items.length > 0 && (
          <button
            onClick={onBatchClose}
            disabled={confirming || batch.isPending}
            className="inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            {(confirming || batch.isPending)
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Users className="h-4 w-4" />}
            Batch check-out
          </button>
        )}
      </div>

      {batch.error && (
        <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-sm text-danger-600">
          {batch.error.message}
        </p>
      )}
      {batch.data && (
        <p className="mt-3 rounded-md bg-success-50 px-3 py-2 text-sm text-success-600">
          Closed {batch.data.closed} visit{batch.data.closed === 1 ? '' : 's'}.
        </p>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full divide-y divide-neutral-200">
          <thead className="bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-4 py-2.5">Visit</th>
              <th className="px-4 py-2.5">Area</th>
              <th className="px-4 py-2.5 hidden md:table-cell">Checked In</th>
              <th className="px-4 py-2.5">Duration</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 bg-white text-sm">
            {isLoading && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-neutral-400">Loading…</td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-12 text-center text-neutral-400">
                  No visitors are currently on-site.
                </td>
              </tr>
            )}
            {items.map(v => (
              <tr key={v.id} className="hover:bg-primary-50/30">
                <td className="px-4 py-2.5">
                  <Link to={`/${v.id}`} className="text-primary-700 hover:underline">
                    {v.visitor
                      ? `${v.visitor.first_name} ${v.visitor.last_name}`
                      : `Visit #${v.id.slice(0, 8)}`}
                  </Link>
                  {v.visitor?.company_name && (
                    <p className="text-xs text-neutral-500">{v.visitor.company_name}</p>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <AccessAreaBadge area={v.access_area} />
                </td>
                <td className="px-4 py-2.5 hidden md:table-cell text-neutral-600">
                  {formatDateTime(v.actual_arrival)}
                </td>
                <td className="px-4 py-2.5 text-neutral-600">
                  {timeAgo(v.actual_arrival)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
