/** Standalone browser for signed health declarations (PRD §2.2.2 / VMS-CI-010).
 *
 * Visibility-scoped server-side (auditor/admin see all; host sees own). Filter
 * by visit-date range and visitor name/company; click a row to view the signed
 * declaration (answers + e-signature) in a read-only modal.
 */
import { useState } from 'react'
import { Eye, Search, ShieldCheck, ShieldAlert } from 'lucide-react'
import {
  useBrowseHealthDeclarations, type HealthDeclarationListItem,
} from '@/services/api'
import { HealthDeclView } from '@/components/HealthDeclView'
import { formatDate, formatDateTime } from '@/lib/utils'

const PAGE_SIZE = 25

export default function HealthDeclarationsPage() {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [view, setView] = useState<HealthDeclarationListItem | null>(null)

  const { data, isLoading, error } = useBrowseHealthDeclarations({
    from, to, q, page, page_size: PAGE_SIZE,
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <h1 className="text-2xl font-bold text-neutral-900">Health declarations</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Signed GMP / Laboratory visitor health declarations.
      </p>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-xs text-neutral-600">
          From
          <input
            type="date" value={from}
            onChange={(e) => { setFrom(e.target.value); setPage(1) }}
            className="mt-1 block rounded-md border border-neutral-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-neutral-600">
          To
          <input
            type="date" value={to}
            onChange={(e) => { setTo(e.target.value); setPage(1) }}
            className="mt-1 block rounded-md border border-neutral-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="min-w-[220px] flex-1 text-xs text-neutral-600">
          Search
          <div className="relative mt-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setPage(1) }}
              placeholder="Visitor name or company"
              className="block w-full rounded-md border border-neutral-300 py-1 pl-7 pr-2 text-sm"
            />
          </div>
        </label>
      </div>

      {/* Table */}
      <div className="mt-4 overflow-x-auto rounded-md border border-neutral-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b border-neutral-100 bg-neutral-50 text-left text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-3 py-2">Date</th>
              <th className="px-3 py-2">Visitor</th>
              <th className="px-3 py-2">Company</th>
              <th className="px-3 py-2">Host</th>
              <th className="px-3 py-2">Area</th>
              <th className="px-3 py-2">Result</th>
              <th className="px-3 py-2">Submitted</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {isLoading && (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No declarations found.</td></tr>
            )}
            {items.map((it) => (
              <tr key={it.id} className="hover:bg-neutral-50">
                <td className="px-3 py-2">{formatDate(it.visit_date)}</td>
                <td className="px-3 py-2 font-medium text-neutral-800">{it.visitor_name}</td>
                <td className="px-3 py-2 text-neutral-600">{it.company}</td>
                <td className="px-3 py-2 text-neutral-600">{it.host_name}</td>
                <td className="px-3 py-2 text-neutral-600">{it.access_area.replace(/_/g, ' ')}</td>
                <td className="px-3 py-2">
                  <span className={
                    'inline-flex items-center gap-1 text-xs font-medium ' +
                    (it.result === 'passed' ? 'text-success-600' : 'text-danger-600')
                  }>
                    {it.result === 'passed'
                      ? <ShieldCheck className="h-3.5 w-3.5" />
                      : <ShieldAlert className="h-3.5 w-3.5" />}
                    {it.result}
                  </span>
                </td>
                <td className="px-3 py-2 text-neutral-500">{formatDateTime(it.updated_at)}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => setView(it)}
                    className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
                  >
                    <Eye className="h-3.5 w-3.5" /> View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && <p className="mt-2 text-xs text-danger-600">{error.message}</p>}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-xs text-neutral-500">
          <span>{total} total</span>
          <div className="flex items-center gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40"
            >
              Prev
            </button>
            <span>Page {page} / {totalPages}</span>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {view && (
        <HealthDeclView
          visitorName={view.visitor_name}
          result={view.result}
          answers={view.questionnaire_data.answers}
          signature={view.signature}
          submittedAt={view.updated_at}
          onClose={() => setView(null)}
        />
      )}
    </div>
  )
}
