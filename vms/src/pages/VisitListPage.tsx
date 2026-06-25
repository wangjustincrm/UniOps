import { Link } from 'react-router-dom'
import { Plus, AlertCircle } from 'lucide-react'
import { useVisits, type Visit } from '@/services/api'
import { StatusBadge, AccessAreaBadge } from '@/components/StatusBadge'
import { formatDateTime } from '@/lib/utils'

export default function VisitListPage({ scope }: { scope: 'today' | 'all' }) {
  const today = new Date().toISOString().slice(0, 10)
  const filters = scope === 'today'
    ? { date_from: today, date_to: today, page_size: 100 }
    : { page_size: 50 }

  const { data, isLoading, error } = useVisits(filters)
  const items = data?.items ?? []

  const title = scope === 'today' ? 'Today’s Visits' : 'All Visits'
  const subtitle = scope === 'today'
    ? `${new Date().toLocaleDateString('en-CA', { weekday: 'long', month: 'short', day: 'numeric' })}`
    : `${data?.total ?? 0} total`

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">{title}</h1>
          <p className="mt-1 text-sm text-neutral-500">{subtitle}</p>
        </div>
        <Link
          to="/new"
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700"
        >
          <Plus className="h-4 w-4" />
          New Visit
        </Link>
      </div>

      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
          <AlertCircle className="h-4 w-4" />
          {error.message}
        </div>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full divide-y divide-neutral-200">
          <thead className="bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-4 py-2.5">Visitor</th>
              <th className="px-4 py-2.5 hidden md:table-cell">Company</th>
              <th className="px-4 py-2.5 hidden md:table-cell">Planned Arrival</th>
              <th className="px-4 py-2.5">Area</th>
              <th className="px-4 py-2.5">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 bg-white text-sm">
            {isLoading && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-neutral-400">Loading…</td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-12 text-center text-neutral-400">
                  No visits {scope === 'today' ? 'today' : 'on record'} yet.
                </td>
              </tr>
            )}
            {items.map((v: Visit) => (
              <tr key={v.id} className="hover:bg-primary-50/30">
                <td className="px-4 py-2.5">
                  <Link to={`/${v.id}`} className="text-primary-700 hover:underline">
                    {v.visitor
                      ? `${v.visitor.first_name} ${v.visitor.last_name}`
                      : `Visit #${v.id.slice(0, 8)}`}
                  </Link>
                </td>
                <td className="px-4 py-2.5 hidden md:table-cell text-neutral-600">
                  {v.visitor?.company_name ?? '—'}
                </td>
                <td className="px-4 py-2.5 hidden md:table-cell text-neutral-600">
                  {formatDateTime(v.planned_arrival)}
                </td>
                <td className="px-4 py-2.5">
                  <AccessAreaBadge area={v.access_area} />
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={v.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
