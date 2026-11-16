/**
 * ★ UAT 13: "build a separate risk register on all food safety near misses."
 *
 * One row per assessed food-safety near miss, by its latest Food Safety Risk
 * Assessment; highest RPN first. Searched, sorted and paged on the server, as
 * every list here is.
 */
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { LoadFailedNote, PageHeader } from '@/components/ui'
import { SearchBox } from '@/components/ListView'
import TabLink from '@/components/TabLink'
import { BandBadge, useFoodSafetyScales } from '@/components/FoodSafetyRisk'

interface Row {
  incident_id: string; incident_no: string; title: string; occurred_at: string | null
  location_path: string | null; status: string; ccp_failure: boolean
  probability: number; severity: number; detectability: number; rpn: number; band: string
  assessed_by_name: string | null; assessed_at: string
}
type SortKey = 'incident_no' | 'title' | 'occurred_at' | 'rpn' | 'assessed_at'
const TH = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500'

export default function FoodSafetyRegisterPage() {
  const navigate = useNavigate()
  const scales = useFoodSafetyScales()
  const bands = scales.data?.bands ?? []
  const [search, setSearch] = useState('')
  // UAT 14: the Food Safety dashboard's ratings open this at their band.
  const [params] = useSearchParams()
  const [band, setBand] = useState(() => {
    const b = params.get('band') ?? ''
    return ['critical', 'high', 'medium', 'low'].includes(b) ? b : ''
  })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), ...sortParams(sort) })
  if (search.trim()) qs.set('q', search.trim())
  if (band) qs.set('band', band)
  // ★ Review R14: opened from the Food Safety dashboard, the same year it counted.
  const from = params.get('occurred_from'), to = params.get('occurred_to')
  const [period, setPeriod] = useState(Boolean(from && to))
  if (period && from && to) { qs.set('occurred_from', from); qs.set('occurred_to', to) }
  const list = useQuery({
    queryKey: ['food-safety-register', qs.toString()],
    queryFn: () => api.get<{ items: Row[]; total: number }>(`/api/v1/food-safety/register?${qs}`),
    placeholderData: (prev) => prev,
  })
  const rows = list.data?.items ?? []
  const chip = (on: boolean) =>
    `min-h-[36px] rounded-full px-3 text-xs font-medium ${on
      ? 'bg-primary-600 text-white' : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200'}`

  return (
    <div className="p-4">
      <PageHeader title="Food Safety Risk Register"
                  subtitle="Every food safety near miss that has been assessed, by its latest Food Safety Risk Assessment. RPN = Severity × Probability × Detectability; a Critical Control Point failure is 46." />
      <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1) }}
                 placeholder="Search by incident number or title" />
      {period && from && to && (
        <p className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-primary-50 px-3 py-1.5 text-xs text-primary-800">
          Near misses that happened {formatDate(from)} – {formatDate(to)}, as on the Food Safety dashboard.
          <button type="button" className="font-semibold underline" onClick={() => { setPeriod(false); setPage(1) }}>
            Show all
          </button>
        </p>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <button type="button" className={chip(band === '')} aria-pressed={band === ''}
                onClick={() => { setBand(''); setPage(1) }}>All</button>
        {bands.map((b) => (
          <button key={b.code} type="button" className={chip(band === b.code)} aria-pressed={band === b.code}
                  title={b.action} onClick={() => { setBand(b.code); setPage(1) }}>{b.label}</button>
        ))}
      </div>
      {list.isError && <LoadFailedNote what="The register" />}
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-17rem)] overflow-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="Incident" sortKey="incident_no" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="What happened" sortKey="title" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="When" sortKey="occurred_at" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>P × S × D</th>
                <SortTh label="RPN" sortKey="rpn" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>Band</th>
                <SortTh label="Assessed" sortKey="assessed_at" sort={sort} onSort={onSort} className={TH} />
              </tr>
            </thead>
            <tbody>
              {list.isPending && (
                <tr><td colSpan={7} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {list.data && rows.length === 0 && (
                <tr><td colSpan={7} className="p-8 text-center text-sm text-neutral-500">
                  {search.trim() || band ? 'Nothing matches.' : 'No food safety near miss has been assessed yet.'}
                </td></tr>
              )}
              {rows.map((r) => (
                <tr key={r.incident_id} onClick={() => navigate(`/incidents/${r.incident_id}`)}
                    className="cursor-pointer border-t border-neutral-100 hover:bg-primary-50/40">
                  <td className="px-3 py-2">
                    <TabLink to={`/incidents/${r.incident_id}`} onClick={(e) => e.stopPropagation()}
                             className="font-mono text-xs text-primary-700 hover:underline">{r.incident_no}</TabLink>
                  </td>
                  <td className="px-3 py-2">
                    <span className="font-medium text-neutral-900">{r.title}</span>
                    {r.location_path && <span className="block text-xs text-neutral-500">{r.location_path.split('/').join(' › ')}</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{r.occurred_at ? formatDate(r.occurred_at) : '—'}</td>
                  <td className="px-3 py-2 text-xs tabular-nums text-neutral-600">
                    {r.probability} × {r.severity} × {r.detectability}
                    {r.ccp_failure && <span className="block font-semibold text-danger-700">CCP failure</span>}
                  </td>
                  <td className="px-3 py-2 text-base font-semibold tabular-nums">{r.rpn}</td>
                  <td className="px-3 py-2"><BandBadge band={r.band} bands={bands} /></td>
                  <td className="px-3 py-2 text-xs text-neutral-600">
                    {formatDate(r.assessed_at)}{r.assessed_by_name && <span className="block">{r.assessed_by_name}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={list.data?.total ?? 0} onPageChange={setPage}
                    onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
    </div>
  )
}
