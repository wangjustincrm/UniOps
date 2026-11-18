/** Standalone browser for signed health declarations (PRD §2.2.2 / VMS-CI-010).
 *
 * Visibility-scoped server-side (auditor/admin see all; host sees own). Filter
 * by visit-date range and visitor name/company; click a row to view the signed
 * declaration (answers + e-signature) in a read-only modal.
 */
import { useState } from 'react'
import { SortTh, useSort, sortParams, Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { Eye, Search, ShieldCheck, ShieldAlert } from 'lucide-react'
import {
  useAreaRules, useBrowseHealthDeclarations, type HealthDeclarationListItem,
} from '@/services/api'
import { zoneName } from '@/lib/visitOptions'
import { HealthDeclView } from '@/components/HealthDeclView'
import { formatDate, formatDateTime } from '@/lib/utils'

const PAGE_SIZE = 25

export default function HealthDeclarationsPage() {
  const { data: areaRules } = useAreaRules()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [view, setView] = useState<HealthDeclarationListItem | null>(null)
  const [sort, onSort] = useSort<'visit_date' | 'visitor_name' | 'company' | 'host_name' | 'access_area' | 'result' | 'updated_at'>(
    null, () => setPage(1))

  const { data, isLoading, error } = useBrowseHealthDeclarations({
    from, to, q, page, page_size: PAGE_SIZE, ...sortParams(sort),
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <h1 className="text-2xl font-semibold text-fg">Health declarations</h1>
      <p className="mt-1 text-sm text-fg-muted">
        Signed GMP / Laboratory visitor health declarations.
      </p>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-xs text-fg-secondary">
          From
          <Input inputSize="sm"
            type="date" value={from}
            onChange={(e) => { setFrom(e.target.value); setPage(1) }}
            className="mt-1 block"
          />
        </label>
        <label className="text-xs text-fg-secondary">
          To
          <Input inputSize="sm"
            type="date" value={to}
            onChange={(e) => { setTo(e.target.value); setPage(1) }}
            className="mt-1 block"
          />
        </label>
        <label className="min-w-[220px] flex-1 text-xs text-fg-secondary">
          Search
          <div className="relative mt-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted" />
            <Input inputSize="sm"
              value={q}
              onChange={(e) => { setQ(e.target.value); setPage(1) }}
              placeholder="Visitor name or company"
              className="block w-full pl-7 pr-2"
            />
          </div>
        </label>
      </div>

      {/* Table */}
      <div className="mt-4 overflow-x-auto rounded-md border border-line bg-surface">
        <Table density="compact">
          <THead>
            <Tr>
              <SortTh label="Date" sortKey="visit_date" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Visitor" sortKey="visitor_name" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Company" sortKey="company" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Host" sortKey="host_name" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Zone" sortKey="access_area" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Result" sortKey="result" sort={sort} onSort={onSort} className="px-3 py-2" />
              <SortTh label="Submitted" sortKey="updated_at" sort={sort} onSort={onSort} className="px-3 py-2" />
              <Th />
            </Tr>
          </THead>
          <TBody>
            {isLoading && (
              <Tr><Td align="center" colSpan={8} className="text-fg-muted">Loading…</Td></Tr>
            )}
            {!isLoading && items.length === 0 && (
              <Tr><Td align="center" colSpan={8} className="text-fg-muted">No declarations found.</Td></Tr>
            )}
            {items.map((it) => (
              <Tr key={it.id} className="hover:bg-surface-subtle">
                <Td>{formatDate(it.visit_date)}</Td>
                <Td className="font-medium text-fg">{it.visitor_name}</Td>
                <Td className="text-fg-secondary">{it.company}</Td>
                <Td className="text-fg-secondary">{it.host_name}</Td>
                <Td className="text-fg-secondary">{zoneName(areaRules, it.access_area)}</Td>
                <Td>
                  <span className={
                    'inline-flex items-center gap-1 text-xs font-medium ' +
                    (it.result === 'passed' ? 'text-success-fg' : 'text-danger-fg')
                  }>
                    {it.result === 'passed'
                      ? <ShieldCheck className="h-3.5 w-3.5" />
                      : <ShieldAlert className="h-3.5 w-3.5" />}
                    {it.result}
                  </span>
                </Td>
                <Td className="text-fg-muted">{formatDateTime(it.updated_at)}</Td>
                <Td align="right">
                  <Button variant="secondary" size="xs"
                    onClick={() => setView(it)}
                   
                  >
                    <Eye className="h-3.5 w-3.5" /> View
                  </Button>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>

      {error && <p className="mt-2 text-xs text-danger-fg">{error.message}</p>}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-xs text-fg-muted">
          <span>{total} total</span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="xs"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
             
            >
              Prev
            </Button>
            <span>Page {page} / {totalPages}</span>
            <Button variant="secondary" size="xs"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
             
            >
              Next
            </Button>
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
