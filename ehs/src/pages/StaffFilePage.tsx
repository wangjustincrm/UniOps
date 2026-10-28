/**
 * The staff file: everything in Safety with one person's name on it.
 *
 * Round 10 (open box): "create a staff register where the hse and supervisor
 * roles can enter a staffs name, and see every form, incident, near miss,
 * hazard assessment, document, position, jhsc meeting, or anything else that
 * has the workers name on it. Sort it into a dashboard with register groups
 * with a total count of items in that group. provide an excel based list view
 * under this … make each line item a link to the item for viewing."
 *
 * The server decides whose file a viewer may open and which groups they see
 * (services/staff_file.py): a group they may not open is not there at all,
 * rather than 0. This page shows what it is given and adds nothing.
 *
 * `/staff-file` picks the person; `/staff-file/:id` is that person's file, a
 * tab of its own per person.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Download, Search } from 'lucide-react'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { api, useFileOpener } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import TabLink from '@/components/TabLink'
import {
  Card, Empty, inputClass, LoadProblem, PageHeader, secondaryButton,
} from '@/components/ui'

interface Person { id: string; full_name: string; email: string | null; department: string | null; is_active: boolean }
interface Group { key: string; label: string; count: number }
interface Row {
  category: string; register: string; date: string | null
  number: string | null; title: string | null; path: string | null
}
interface StaffFile { person: Person; groups: Group[]; rows: Row[]; total: number }

export function StaffFilePicker() {
  const [term, setTerm] = useState('')
  const navigate = useNavigate()
  const people = useQuery({
    queryKey: ['staff-file', 'people', term.trim()],
    queryFn: () => api.get<Person[]>(
      `/api/v1/staff-file/people${term.trim() ? `?search=${encodeURIComponent(term.trim())}` : ''}`),
  })
  return (
    <div className="mx-auto max-w-3xl p-4">
      <PageHeader title="Staff file"
                  subtitle="Everything in Safety with one person's name on it." />
      <label className="relative mb-3 block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" aria-hidden />
        <input className={`${inputClass} pl-9`} value={term} aria-label="Find a person"
               placeholder="Find a person by name or email" onChange={(e) => setTerm(e.target.value)} />
      </label>
      {people.isError && <LoadProblem error={people.error} noun="people" />}
      {people.isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      {people.data?.length === 0 && <Empty>Nobody you can open by that name.</Empty>}
      <ul className="flex flex-col gap-1.5">
        {(people.data ?? []).map((p) => (
          <li key={p.id}>
            <button type="button" onClick={() => navigate(`/staff-file/${p.id}`)}
                    className="flex w-full items-baseline gap-3 rounded-lg border border-neutral-200 bg-white px-3 py-2.5 text-left hover:border-primary-300">
              <span className="text-sm font-medium text-neutral-900">{p.full_name}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-neutral-500">
                {[p.department, p.email].filter(Boolean).join(' · ')}
              </span>
              {!p.is_active && (
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">Left</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function StaffFilePage() {
  const { id = '' } = useParams()
  const opener = useFileOpener()
  const [only, setOnly] = useState<string | null>(null)
  const file = useQuery({
    queryKey: ['staff-file', id],
    queryFn: () => api.get<StaffFile>(`/api/v1/staff-file/${id}`),
    enabled: Boolean(id),
    retry: false,
  })
  useTabTitle(ehsRoutes, file.data?.person.full_name)

  if (file.isPending) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (file.isError || !file.data) return <LoadProblem error={file.error} noun="staff file" />
  const f = file.data
  const rows = only ? f.rows.filter((r) => r.category === only) : f.rows

  return (
    <div className="p-4">
      <PageHeader title={f.person.full_name}
                  subtitle={[f.person.department, f.person.email, f.person.is_active ? null : 'Has left']
                    .filter(Boolean).join(' · ') || undefined}>
        <button type="button" className={secondaryButton} disabled={opener.busy}
                onClick={() => opener.open(`/api/v1/staff-file/${id}/export`,
                                           `staff-file-${f.person.full_name}.xlsx`)}>
          <Download className="h-4 w-4" aria-hidden />
          {opener.busy ? 'Exporting…' : 'Export'}
        </button>
      </PageHeader>

      {f.groups.length === 0 ? (
        <Empty>There is nothing here you can open for this person.</Empty>
      ) : (
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6" aria-label="Register groups">
          {f.groups.map((g) => (
            <button key={g.key} type="button" aria-pressed={only === g.key}
                    onClick={() => setOnly(only === g.key ? null : g.key)}
                    className={`rounded-lg border px-3 py-2 text-left ${only === g.key
                      ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white hover:border-primary-300'}`}>
              <span className="block text-xl font-semibold tabular-nums text-primary-700">{g.count}</span>
              <span className="text-xs text-neutral-600">{g.label}</span>
            </button>
          ))}
        </div>
      )}

      {f.total > f.rows.length && (
        <p className="mb-2 text-xs text-warning-700">
          The newest {f.rows.length} of {f.total} are listed; the export holds the same.
        </p>
      )}
      {rows.length > 0 && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-neutral-50 text-left text-xs font-semibold text-neutral-600">
              <tr>
                <th className="px-3 py-2">Employee name</th>
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Register type</th>
                <th className="px-3 py-2">Number</th>
                <th className="px-3 py-2">Title</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {rows.map((r, n) => (
                <tr key={`${r.category}-${n}`}>
                  <td className="px-3 py-2 text-neutral-800">{f.person.full_name}</td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums text-neutral-600">
                    {r.date ? formatDate(r.date.slice(0, 10)) : '—'}
                  </td>
                  <td className="px-3 py-2 text-neutral-600">{r.register}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {r.path
                      ? <TabLink to={r.path} className="text-primary-700 hover:underline">{r.number ?? 'Open'}</TabLink>
                      : (r.number ?? '—')}
                  </td>
                  <td className="px-3 py-2 text-neutral-800">{r.title}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <p className="mt-3 text-xs text-neutral-500">
        <Link to="/staff-file" className="font-medium text-primary-700 hover:underline">Another person</Link>
      </p>
    </div>
  )
}
