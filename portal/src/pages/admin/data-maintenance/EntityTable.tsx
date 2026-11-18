import { useEffect, useState } from 'react'
import { SortTh, useSort, Checkbox, Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import type { EntitySchema } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminList } from '@/hooks/useAdmin'
import { BulkDeleteConfirm } from './BulkDeleteConfirm'
import { formatDate, formatDateTime } from '@/lib/utils'

// Column headers were the raw field names ("VENDOR_NAME", ISS-0183).
const columnLabel = (c: string) => c.replace(/_/g, ' ')

// ISO dates / timestamps print as YYYY-MMM-DD [HH:MM] (UI-DATE-002; was
// DD/MM/YYYY under UI-DATE-001, ISS-0183). Like every other screen, a
// timestamp with a zone is shown on the plant clock (Company Settings time
// zone, UI-DATE-003) — it used to stay in UTC with a "UTC" tag. Plain dates
// and zone-less wall-clock values are printed as written.
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/
const ISO_TS = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/
function cellText(v: unknown): string {
  const s = String(v ?? '')
  const d = ISO_DATE.exec(s)
  if (d) return formatDate(s)
  if (ISO_TS.test(s)) return formatDateTime(s)
  return s
}

interface Props {
  schema: EntitySchema
  onEdit: (record: Record<string, unknown>) => void
  onDelete: (record: Record<string, unknown>) => void
}

export function EntityTable({ schema, onEdit, onDelete }: Props) {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [allMatching, setAllMatching] = useState(false) // true once "select all N matching" is used
  const [bulkOpen, setBulkOpen] = useState(false)
  const [loadingAll, setLoadingAll] = useState(false)
  // Server-sorted (the list pages): the API whitelists schema.sortable_columns.
  const [sort, onSort, setSort] = useSort<string>(null, () => setPage(1))
  useEffect(() => { setSort(null) }, [schema.key, setSort])
  const { data, isLoading } = useAdminList(schema.system, schema.key, page, search, sort)

  const cols = schema.list_columns
  const sortable = new Set(schema.sortable_columns ?? [])
  // VMS compliance records are edit-only (backend refuses delete with 409).
  const canDelete = schema.allow_delete !== false
  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1
  const pageIds = (data?.items ?? []).map((r) => String(r.id))
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))

  // Reset selection whenever the result set changes (entity / page / search).
  useEffect(() => { setSelected(new Set()); setAllMatching(false) }, [schema.key, page, search, sort])

  const toggleRow = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const togglePage = () =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (allPageSelected) pageIds.forEach((id) => next.delete(id))
      else pageIds.forEach((id) => next.add(id))
      return next
    })

  const selectAllMatching = async () => {
    setLoadingAll(true)
    try {
      const ids = await adminApi.allIds(schema.system, schema.key, search || undefined)
      setSelected(new Set(ids))
      setAllMatching(true)
    } finally {
      setLoadingAll(false)
    }
  }

  const clearSelection = () => { setSelected(new Set()); setAllMatching(false) }

  const total = data?.total ?? 0
  const showSelectAllMatching = allPageSelected && !allMatching && total > pageIds.length

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Input inputSize="sm"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          placeholder={`Search ${schema.label}…`}
          className="w-72"
        />
        {canDelete && selected.size > 0 && (
          <div className="flex items-center gap-2">
            <Button variant="danger" size="sm"
              onClick={() => setBulkOpen(true)}
             
            >
              Delete selected ({selected.size})
            </Button>
            <Button variant="link" onClick={clearSelection} className="text-sm text-fg-muted">Clear</Button>
          </div>
        )}
      </div>

      {canDelete && (showSelectAllMatching || allMatching) && (
        <div className="rounded-lg bg-warning-soft border border-warning-line px-3 py-2 text-xs text-warning-fg">
          {allMatching ? (
            <>All <strong>{selected.size}</strong> matching {schema.label} records are selected.</>
          ) : (
            <>
              All {pageIds.length} on this page selected.{' '}
              <Button variant="link" onClick={selectAllMatching} disabled={loadingAll}>
                {loadingAll ? 'Selecting…' : `Select all ${total} matching`}
              </Button>
            </>
          )}
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-line">
        <Table density="compact">
          <THead>
            <Tr>
              {canDelete && (
                <Th className="w-10">
                  <Checkbox aria-label="Select all on page"
                    checked={allPageSelected} onChange={togglePage}
                    />
                </Th>
              )}
              {cols.map((c) => sortable.has(c) ? (
                <SortTh key={c} label={columnLabel(c)} sortKey={c} sort={sort} onSort={onSort}
                  className="px-3 py-2 text-left text-xs font-semibold uppercase text-fg-muted" />
              ) : (
                <Th key={c}>{columnLabel(c)}</Th>
              ))}
              <Th />
            </Tr>
          </THead>
          <TBody>
            {isLoading && <Tr><Td align="center" colSpan={cols.length + (canDelete ? 2 : 1)} className="text-fg-muted">Loading…</Td></Tr>}
            {data?.items.map((row) => {
              const id = String(row.id)
              return (
                <Tr key={id} className={`border-t border-line-subtle ${selected.has(id) ? 'bg-primary-50/40' : ''}`}>
                  {canDelete && (
                    <Td>
                      <Checkbox aria-label="Select row"
                        checked={selected.has(id)} onChange={() => toggleRow(id)}
                        />
                    </Td>
                  )}
                  {cols.map((c) => (
                    <Td key={c} className="text-fg">{cellText(row[c])}</Td>
                  ))}
                  <Td align="right" className="whitespace-nowrap">
                    {schema.allow_edit !== false && (
                      <Button variant="link" onClick={() => onEdit(row)} className="text-xs">Edit</Button>
                    )}
                    {canDelete && (
                      <Button variant="link" onClick={() => onDelete(row)} className="text-xs text-danger-fg">Delete</Button>
                    )}
                  </Td>
                </Tr>
              )
            })}
            {data && data.items.length === 0 && (
              <Tr><Td align="center" colSpan={cols.length + (canDelete ? 2 : 1)} className="text-fg-muted">No records</Td></Tr>
            )}
          </TBody>
        </Table>
      </div>

      <div className="flex items-center gap-3 text-sm">
        <Button variant="secondary" size="xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</Button>
        <span>Page {page} / {totalPages} · {total} total</span>
        <Button variant="secondary" size="xs" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
      </div>

      {bulkOpen && (
        <BulkDeleteConfirm
          schema={schema}
          ids={Array.from(selected)}
          onClose={() => setBulkOpen(false)}
          onDone={clearSelection}
        />
      )}
    </div>
  )
}
