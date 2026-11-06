// NC warehouse classes (design §4.6). The list is the union of what NC has
// and what has been classified here, so a warehouse NC added since the last
// edit shows up as unclassified instead of being invisible — and an
// unclassified warehouse never takes part in the WMS comparison.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, SortTh, useSortableRows } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/lib/time'
import type { useToasts } from '@/hooks/useToasts'
import { settingsApi, type WarehouseClass, type WarehouseClassRow } from './settingsApi'

const CLASS_LABELS: Record<WarehouseClass, string> = {
  wms: 'Managed by WMS — compared',
  external: 'Outside consignment — listed separately',
  production: 'Production line-side — listed separately',
  transient: 'Transient — not listed, still counted in NC total',
  other: 'Other',
}

const CLASS_ORDER: WarehouseClass[] = ['wms', 'external', 'production', 'transient', 'other']

const selectCls =
  'h-9 w-full min-w-[16rem] rounded-lg border border-neutral-200 bg-neutral-100 px-2 text-sm text-neutral-900 focus:outline-none focus:bg-white focus:border-primary-600'

function Row({ row, canAdmin, toasts }: {
  row: WarehouseClassRow
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
}) {
  const queryClient = useQueryClient()
  const original = { cls: row.warehouse_class, note: row.note ?? '' }
  const [cls, setCls] = useState<string>(original.cls)
  const [note, setNote] = useState(original.note)
  const [error, setError] = useState<string | null>(null)
  const dirty = cls !== original.cls || note !== original.note

  const save = useMutation({
    mutationFn: () => settingsApi.putWarehouseClass(row.code, cls as WarehouseClass, note.trim() || null),
    onMutate: () => setError(null),
    onSuccess: () => toasts.success(`${row.code} saved.`),
    onError: (err) => setError(err instanceof Error ? err.message : 'Save failed.'),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['inv-warehouse-classes'] }),
  })

  const unclassified = row.warehouse_class === 'unclassified'

  return (
    <tr className={cn('border-b border-neutral-100 align-top last:border-0', unclassified && 'bg-warning-50/60')}>
      <td className="px-3 py-2 font-mono text-xs">
        {row.code}
        {!row.in_nc && <Badge variant="neutral" className="ml-2">Not in NC</Badge>}
      </td>
      <td className="px-3 py-2">
        {row.name ?? '—'}
        {row.costed === false && <Badge variant="warning" className="ml-2">Not costed</Badge>}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(row.on_hand_qty)}</td>
      <td className="px-3 py-2">
        {canAdmin ? (
          <select
            className={selectCls} value={cls} aria-label={`Class for ${row.code}`}
            onChange={(e) => setCls(e.target.value)}
          >
            {cls === 'unclassified' && <option value="unclassified" disabled>Unclassified — choose…</option>}
            {CLASS_ORDER.map((c) => <option key={c} value={c}>{CLASS_LABELS[c]}</option>)}
          </select>
        ) : unclassified ? (
          <Badge variant="warning">Unclassified</Badge>
        ) : (
          CLASS_LABELS[row.warehouse_class as WarehouseClass] ?? row.warehouse_class
        )}
      </td>
      <td className="px-3 py-2">
        {canAdmin ? (
          <Input
            className="h-9" value={note} maxLength={500} aria-label={`Note for ${row.code}`}
            onChange={(e) => setNote(e.target.value)}
          />
        ) : (
          <span className="text-neutral-600">{row.note || '—'}</span>
        )}
        {error && <p role="alert" className="mt-1 text-xs text-danger-600">{error}</p>}
      </td>
      {canAdmin && (
        <td className="px-3 py-2 text-right">
          <Button
            size="sm" variant="secondary"
            disabled={!dirty || cls === 'unclassified' || save.isPending}
            onClick={() => { if (!save.isPending) save.mutate() }}
          >
            {save.isPending && <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />}
            Save
          </Button>
        </td>
      )}
    </tr>
  )
}

export function WarehouseClassesCard({ canAdmin, toasts }: {
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
}) {
  const q = useQuery({ queryKey: ['inv-warehouse-classes'], queryFn: settingsApi.warehouseClasses })
  const data = q.data
  // Sorted on the SAVED values, so a row being edited does not jump away.
  const { rows, sort, onSort } = useSortableRows(data?.items, {
    code: (r) => r.code,
    name: (r) => r.name,
    qty: (r) => (r.on_hand_qty == null ? null : Number(r.on_hand_qty)),
    cls: (r) => (r.warehouse_class === 'unclassified'
      ? 'Unclassified' : CLASS_LABELS[r.warehouse_class as WarehouseClass] ?? r.warehouse_class),
    note: (r) => r.note,
  })
  const th = 'px-3 py-2 font-medium'

  return (
    <Card>
      <CardHeader>
        <CardTitle>NC warehouse classes</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {q.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
        {q.isError && <p className="text-sm text-danger-600">Could not load warehouse classes: {(q.error as Error).message}</p>}
        {data && data.unclassified_count > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700">
            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
            {data.unclassified_count} NC warehouse{data.unclassified_count === 1 ? ' is' : 's are'} unclassified
            — {data.unclassified_count === 1 ? 'it is' : 'they are'} excluded from the WMS comparison until classified.
          </div>
        )}
        {data && (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-xs text-neutral-500 sm:grid-cols-2">
            {CLASS_ORDER.map((c) => (
              <div key={c} className="flex gap-2">
                <dt className="w-20 shrink-0 font-mono text-neutral-700">{c}</dt>
                <dd>{CLASS_LABELS[c]}</dd>
              </div>
            ))}
          </dl>
        )}
        {data && data.items.length === 0 && (
          <p className="text-sm text-neutral-500">No NC warehouses yet — run a sync first.</p>
        )}
        {data && data.items.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50">
                <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
                  <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className={th} />
                  <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className={th} />
                  <SortTh label="On-hand qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className={th} />
                  <SortTh label="Class" sortKey="cls" sort={sort} onSort={onSort} className={th} />
                  <SortTh label="Note" sortKey="note" sort={sort} onSort={onSort} className={th} />
                  {canAdmin && <th className="px-3 py-2" />}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  // Keyed on the saved state too, so a refetch after save
                  // re-seeds the row's local edit state from the server.
                  <Row
                    key={`${row.code}|${row.warehouse_class}|${row.note ?? ''}`}
                    row={row} canAdmin={canAdmin} toasts={toasts}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
