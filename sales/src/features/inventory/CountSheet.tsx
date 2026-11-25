import { z } from 'zod'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, LocalTable, RecordSheet, ViewFileButton, columnsOf, formatDateTime, formatNumber, queryKeys, type ColumnDef, type RecordTab, useLookup, FormDialog, SchemaForm, type FieldSpec, versionOf } from '@/platform-ui'
import { api } from '@/lib/api'
import { applyCount, fetchCountLines, stockCountsClient, stockItemsClient, stockLotsClient, undoCount, updateCount, type StockCountLineRead, type StockCountRead } from './inventoryApi'
import { ADJUST, errorMessages } from './countRules'
import { Comparison } from './CountComparison'

const colLine = columnsOf<StockCountLineRead>()
const lineColumns: ColumnDef<StockCountLineRead, unknown>[] = [
  colLine.text('item_code', 'Item'),
  colLine.text('description', 'Description'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '(no lot)' },
  colLine.date('expiry', 'Expiry'),
  colLine.number('qty_ea', 'Quantity EA'),
  colLine.number('hold_ea', 'On hold EA'),
]

const changeSchema = z.object({
  counted_at: z.string().min(1, 'Set when the report was taken.'),
  note: z.string().trim().max(2000).nullable().optional(),
})

function ChangeDialog({ count, open, onOpenChange }: { count: StockCountRead; open: boolean; onOpenChange: (o: boolean) => void }) {
  const fields: FieldSpec[] = [
    { name: 'counted_at', label: "Taken at (from Radiant's portal)", type: 'datetime', required: true, readOnly: !!count.applied_at,
      hint: count.applied_at ? 'Undo the count to change its moment.' : undefined },
    { name: 'note', label: 'Note', type: 'textarea' },
  ]
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Change the count">
      <SchemaForm schema={changeSchema} fields={fields} defaultValues={{ counted_at: count.counted_at, note: count.note ?? '' }}
        errorMessages={errorMessages} onCancel={() => onOpenChange(false)}
        done="Saved" invalidates={[queryKeys.list(stockCountsClient)]} after={() => onOpenChange(false)}
        version={count.version} onSubmit={(v, edit) => updateCount(count.id, versionOf(edit), count.applied_at ? null : String(v.counted_at), (v.note as string | null)?.trim() || null)} />
    </FormDialog>
  )
}

export function CountSheet({ row }: { row: StockCountRead }) {
  const qc = useQueryClient()
  const [applying, setApplying] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const [changing, setChanging] = useState(false)
  const linesQ = useLookup(['stock-count-lines', row.id], () => fetchCountLines(row.id), { errors: 'inline' })
  const refresh = async () => {
    for (const c of [stockCountsClient, stockItemsClient, stockLotsClient]) await qc.invalidateQueries({ queryKey: queryKeys.list(c) })
    await qc.invalidateQueries({ queryKey: ['stock-count-compare', row.id] })
  }
  const tabs: RecordTab[] = [
    { id: 'compare', label: 'Comparison', render: () => <Comparison count={row} /> },
    { id: 'lines', label: "Radiant's report", count: linesQ.data?.length, render: () => (
      <LocalTable<StockCountLineRead> ariaLabel="Report lines" columns={lineColumns} rows={linesQ.data ?? []} rowKey={(l) => l.id} />
    ) },
  ]
  return (<>
    <RecordSheet
      title={`Radiant inventory · ${formatDateTime(row.counted_at)}`}
      subtitle={row.file_name ?? undefined}
      size="xl"
      facts={[
        { label: 'State', value: row.applied_at ? 'Applied: the start' : 'Report only', tone: row.applied_at ? 'success' : 'neutral',
          hint: row.applied_at ? `by ${row.applied_by_name ?? '—'}, ${formatDateTime(row.applied_at)}` : 'the inventory is not changed' },
        { label: 'Lots', value: formatNumber(row.lines) },
        { label: 'Total EA', value: formatNumber(row.total_ea) },
        { label: 'On hold EA', value: formatNumber(row.hold_ea) },
        { label: 'Uploaded by', value: row.created_by_name },
      ]}
      actions={<>
        {row.document_id != null && (
          <ViewFileButton source={{ key: `document-${row.document_id}`, title: `Radiant inventory: ${row.file_name ?? row.document_id}`,
            load: () => api.file(`/documents/${row.document_id}/raw`, row.file_name ?? 'inventory') }}>Report file</ViewFileButton>
        )}
        <ActionButton size="sm" variant="secondary" permission={ADJUST} onAction={() => setChanging(true)}>Change…</ActionButton>
        {row.applied_at
          ? <ActionButton size="sm" variant="secondary" permission={ADJUST} onAction={() => setUndoing(true)}>Undo…</ActionButton>
          : <ActionButton size="sm" permission={ADJUST} onAction={() => setApplying(true)}>Apply as the start…</ActionButton>}
      </>}
      tabs={tabs}
    />
      <ChangeDialog key={row.id} count={row} open={changing} onOpenChange={setChanging} />
      <ConfirmAction open={applying} onOpenChange={setApplying} title="Apply this report as the start?"
        description={`At ${formatDateTime(row.counted_at)} every lot is set to Radiant's figure (a lot Radiant does not list goes to 0). `
          + 'The moves before are kept; every 944 and 945 after it adds up from there. It can be undone.'}
        confirmLabel="Apply" errorMessages={errorMessages}
        onConfirm={async () => { await applyCount(row.id, row.version); await refresh() }} />
      <ConfirmAction open={undoing} onOpenChange={setUndoing} title="Undo this count?" destructive
        description="Its count moves are taken away: the figures go back to Sales' own moves. The report stays."
        confirmLabel="Undo" errorMessages={errorMessages} reason={{ label: 'Why' }}
        onConfirm={async (why) => { await undoCount(row.id, row.version, why); await refresh() }} />
  </>)
}
