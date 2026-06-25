import { useEffect, useState } from 'react'
import type { EntitySchema } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminList } from '@/hooks/useAdmin'
import { BulkDeleteConfirm } from './BulkDeleteConfirm'

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
  const { data, isLoading } = useAdminList(schema.system, schema.key, page, search)

  const cols = schema.list_columns
  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1
  const pageIds = (data?.items ?? []).map((r) => String(r.id))
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))

  // Reset selection whenever the result set changes (entity / page / search).
  useEffect(() => { setSelected(new Set()); setAllMatching(false) }, [schema.key, page, search])

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
        <input
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          placeholder={`Search ${schema.label}…`}
          className="h-9 w-72 rounded-lg border border-neutral-300 px-3 text-sm"
        />
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setBulkOpen(true)}
              className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700"
            >
              Delete selected ({selected.size})
            </button>
            <button onClick={clearSelection} className="text-sm text-neutral-500 hover:underline">Clear</button>
          </div>
        )}
      </div>

      {(showSelectAllMatching || allMatching) && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
          {allMatching ? (
            <>All <strong>{selected.size}</strong> matching {schema.label} records are selected.</>
          ) : (
            <>
              All {pageIds.length} on this page selected.{' '}
              <button onClick={selectAllMatching} disabled={loadingAll} className="font-semibold underline hover:text-amber-900">
                {loadingAll ? 'Selecting…' : `Select all ${total} matching`}
              </button>
            </>
          )}
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-neutral-200">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50">
            <tr>
              <th className="w-10 px-3 py-2">
                <input type="checkbox" aria-label="Select all on page"
                  checked={allPageSelected} onChange={togglePage}
                  className="h-4 w-4 rounded border-neutral-300" />
              </th>
              {cols.map((c) => (
                <th key={c} className="px-3 py-2 text-left text-xs font-semibold uppercase text-neutral-500">{c}</th>
              ))}
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={cols.length + 2} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>}
            {data?.items.map((row) => {
              const id = String(row.id)
              return (
                <tr key={id} className={`border-t border-neutral-100 ${selected.has(id) ? 'bg-primary-50/40' : ''}`}>
                  <td className="px-3 py-2">
                    <input type="checkbox" aria-label="Select row"
                      checked={selected.has(id)} onChange={() => toggleRow(id)}
                      className="h-4 w-4 rounded border-neutral-300" />
                  </td>
                  {cols.map((c) => (
                    <td key={c} className="px-3 py-2 text-neutral-800">{String(row[c] ?? '')}</td>
                  ))}
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {schema.allow_edit !== false && (
                      <button onClick={() => onEdit(row)} className="text-xs text-primary-600 hover:underline mr-3">Edit</button>
                    )}
                    <button onClick={() => onDelete(row)} className="text-xs text-red-600 hover:underline">Delete</button>
                  </td>
                </tr>
              )
            })}
            {data && data.items.length === 0 && (
              <tr><td colSpan={cols.length + 2} className="px-3 py-6 text-center text-neutral-400">No records</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-3 text-sm">
        <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded border px-2 py-1 disabled:opacity-40">Prev</button>
        <span>Page {page} / {totalPages} · {total} total</span>
        <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} className="rounded border px-2 py-1 disabled:opacity-40">Next</button>
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
