import { useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, Button, Callout, Checkbox, ConfirmAction, StatusCell, DateTimeInput, Dialog, FilePicker, FormField, ListPage, LocalTable, RecordSheet, Section,
  Textarea, ViewFileButton, columnsOf, formatDateTime, formatNumber, queryKeys,
  type ColumnDef, type RecordTab,
} from '@/platform-ui'
import { api } from '@/lib/api'
import {
  COMPARE_KIND, applyCount, fetchCountCompare, fetchCountLines, stockCountsClient, stockItemsClient, stockLotsClient, undoCount, updateCount,
  uploadCount, type CountCompareRow, type StockCountLineRead, type StockCountRead,
} from './inventoryApi'

const ADJUST = 'sales.inventory.adjust'
const MAX_MB = 20
const colCount = columnsOf<StockCountRead>()
const colCmp = columnsOf<CountCompareRow>()
const colLine = columnsOf<StockCountLineRead>()
const errorMessages: Record<string, string> = {
  'stock_count.file': "This file cannot be read as Radiant's inventory report.",
  'stock_count.when': 'Give the moment the report was taken (not in the future).',
  'stock_count.applied': 'Undo the count first.',
  'auth.forbidden': 'You may not change the inventory.',
}

const countColumns: ColumnDef<StockCountRead, unknown>[] = [
  colCount.datetime('counted_at', 'Counted at'),
  { id: 'state', header: 'State', cell: ({ row }) => (row.original.applied_at ? 'Applied: the start' : 'Report only') },
  colCount.text('file_name', 'File'),
  colCount.text('note', 'Note'),
  colCount.text('created_by_name', 'Uploaded by'),
  colCount.datetime('applied_at', 'Applied'),
]

const KIND_TONE = { match: 'success', differs: 'warning', sales_only: 'danger', radiant_only: 'info', no_lot: 'neutral' } as const

function compareColumns(applied: boolean): ColumnDef<CountCompareRow, unknown>[] {
  return [
    { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <span className="font-medium">{row.original.lot ?? '(no lot)'}</span> },
    colCmp.date('expiry', 'Expiry'),
    colCmp.number('received_ea', 'Received (944)'),
    colCmp.number('returned_ea', 'Returned (RA)'),
    colCmp.number('shipped_ea', 'Shipped (945)'),
    colCmp.number('adjusted_ea', 'Adjusted'),
    colCmp.number('sales_ea', 'Sales at count'),
    colCmp.number('radiant_ea', 'Radiant'),
    colCmp.number('hold_ea', 'On hold'),
    colCmp.number('difference_ea', 'Difference (Sales − Radiant)'),
    { accessorKey: 'kind', header: 'Result', cell: ({ row }) => <StatusCell value={COMPARE_KIND[row.original.kind]} tone={KIND_TONE[row.original.kind]} /> },
    colCmp.number('since_ea', 'Moved after'),
    colCmp.number('now_ea', applied ? 'Now' : 'Now, once applied'),
  ]
}

interface ItemGroup {
  item_code: string; description: string | null; lots: CountCompareRow[]
  received_ea: number; returned_ea: number; shipped_ea: number; adjusted_ea: number; sales_ea: number; radiant_ea: number
  hold_ea: number; difference_ea: number; since_ea: number; now_ea: number; differing: number
}

const SUMS = ['received_ea', 'returned_ea', 'shipped_ea', 'adjusted_ea', 'sales_ea', 'radiant_ea', 'hold_ea', 'difference_ea', 'since_ea', 'now_ea'] as const

function group(rows: CountCompareRow[]): ItemGroup[] {
  const by = new Map<string, ItemGroup>()
  for (const r of rows) {
    const g = by.get(r.item_code) ?? {
      item_code: r.item_code, description: r.description, lots: [], differing: 0,
      received_ea: 0, returned_ea: 0, shipped_ea: 0, adjusted_ea: 0, sales_ea: 0, radiant_ea: 0, hold_ea: 0, difference_ea: 0, since_ea: 0, now_ea: 0,
    }
    g.lots.push(r)
    for (const k of SUMS) g[k] += r[k]
    if (r.kind !== 'match') g.differing += 1
    by.set(r.item_code, g)
  }
  return [...by.values()]
}

const colGroup = columnsOf<ItemGroup>()

function Comparison({ count }: { count: StockCountRead }) {
  const [onlyDiff, setOnlyDiff] = useState(false)
  const q = useQuery({ queryKey: ['stock-count-compare', count.id, count.version], queryFn: () => fetchCountCompare(count.id) })
  const groups = useMemo(() => group((q.data ?? []).filter((r) => !onlyDiff || r.kind !== 'match')), [q.data, onlyDiff])
  const lotCols = useMemo(() => compareColumns(!!count.applied_at), [count.applied_at])
  const itemCols: ColumnDef<ItemGroup, unknown>[] = [
    { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => <span className="font-medium">{row.original.item_code}</span> },
    colGroup.text('description', 'Description'),
    { id: 'lots', header: 'Lots', cell: ({ row: { original: g } }) => `${g.lots.length}${g.differing ? ` · ${g.differing} differ` : ''}` },
    ...SUMS.map((k) => colGroup.number(k, ({
      received_ea: 'Received (944)', returned_ea: 'Returned (RA)', shipped_ea: 'Shipped (945)', adjusted_ea: 'Adjusted', sales_ea: 'Sales at count',
      radiant_ea: 'Radiant', hold_ea: 'On hold', difference_ea: 'Difference', since_ea: 'Moved after', now_ea: count.applied_at ? 'Now' : 'Now, once applied',
    } as const)[k])),
  ]
  const all = q.data ?? []
  const differ = all.filter((r) => r.kind !== 'match').length
  return (
    <Section title="Lot by lot, up to the moment of the report" plain>
      {count.note && <Callout label="Note">{count.note}</Callout>}
      <div className="flex flex-wrap items-center gap-3 text-xs text-neutral-600">
        <span>{formatNumber(all.length)} lots · {formatNumber(all.length - differ)} match · {formatNumber(differ)} differ</span>
        <Checkbox checked={onlyDiff} onCheckedChange={(v) => setOnlyDiff(v === true)} label="Only the lots that differ" />
      </div>
      <LocalTable<ItemGroup> ariaLabel="Comparison by item" columns={itemCols} rows={groups} rowKey={(g) => g.item_code}
        fetching={q.isFetching} emptyTitle={q.isLoading ? 'Loading…' : 'Nothing to compare'}
        expand={(g) => (
          <LocalTable<CountCompareRow> ariaLabel={`Lots of ${g.item_code}`} columns={lotCols} rows={g.lots} density="compact"
            rowKey={(r) => `${r.item_code}|${r.lot ?? ''}`} />
        )} />
    </Section>
  )
}

const lineColumns: ColumnDef<StockCountLineRead, unknown>[] = [
  colLine.text('item_code', 'Item'),
  colLine.text('description', 'Description'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '(no lot)' },
  colLine.date('expiry', 'Expiry'),
  colLine.number('qty_ea', 'Quantity EA'),
  colLine.number('hold_ea', 'On hold EA'),
]

function ChangeDialog({ count, open, onOpenChange }: { count: StockCountRead; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient()
  const [when, setWhen] = useState<string | null>(count.counted_at)
  const [note, setNote] = useState(count.note ?? '')
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Change the count"
      footer={<>
        <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
        <ActionButton permission={ADJUST} errorMessages={errorMessages} done="Saved" disabled={!when}
          onAction={async () => {
            await updateCount(count.id, count.version, count.applied_at ? null : when, note.trim() || null)
            await qc.invalidateQueries({ queryKey: queryKeys.list(stockCountsClient) })
            onOpenChange(false)
          }}>Save</ActionButton>
      </>}>
      <div className="space-y-3">
        <FormField label="Taken at (from Radiant's portal)" hint={count.applied_at ? 'Undo the count to change its moment.' : undefined}>
          <DateTimeInput value={when} onChange={setWhen} disabled={!!count.applied_at} aria-label="Taken at" />
        </FormField>
        <FormField label="Note"><Textarea rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" /></FormField>
      </div>
    </Dialog>
  )
}

function CountSheet({ row }: { row: StockCountRead }) {
  const qc = useQueryClient()
  const [applying, setApplying] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const [changing, setChanging] = useState(false)
  const linesQ = useQuery({ queryKey: ['stock-count-lines', row.id], queryFn: () => fetchCountLines(row.id) })
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
      <ChangeDialog key={`${row.id}-${row.version}`} count={row} open={changing} onOpenChange={setChanging} />
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

function UploadForm({ done, cancel }: { done: (c?: StockCountRead) => void; cancel: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [when, setWhen] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const tooLarge = !!file && file.size > MAX_MB * 1024 * 1024
  return (
    <div className="space-y-4">
      <p className="text-sm text-neutral-600">
        The inventory report from Radiant's portal (CSV or Excel), by pallet or by lot. Nothing changes until you apply it.
      </p>
      <div className="flex items-center gap-2">
        <FilePicker label="Report file" accept=".csv,.xlsx,.txt" onFile={setFile}>{file ? 'Choose another file' : 'Choose the file'}</FilePicker>
        <span className="min-w-0 break-all text-sm text-neutral-700">{file ? file.name : 'No file chosen'}</span>
      </div>
      {tooLarge && <p className="text-sm text-danger-600">Too large: at most {MAX_MB} MB.</p>}
      <FormField label="Taken at" hint="When the report was taken from the portal: moves after it are not in it.">
        <DateTimeInput value={when} onChange={setWhen} aria-label="Taken at" />
      </FormField>
      <FormField label="Note"><Textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" /></FormField>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={cancel}>Cancel</Button>
        <ActionButton permission={ADJUST} disabled={!file || !when || tooLarge} errorMessages={errorMessages} done="Uploaded"
          onAction={async () => done(await uploadCount(file!, when!, note.trim() || null))}>Upload</ActionButton>
      </div>
    </div>
  )
}

/** Inventory → Radiant counts (user 2026-11-13): Radiant's reports, each compared lot by lot with Sales' moves. */
export function CountsView({ toolbar }: { toolbar: ReactNode }) {
  return (
    <ListPage<StockCountRead>
      key="counts" resource={stockCountsClient} title="Radiant inventory reports"
      description="Each report from Radiant's portal, compared lot by lot with what Sales recorded; applied, it is where the figures start."
      columns={countColumns} emptyTitle="No report uploaded yet"
      renderDetail={(r) => <CountSheet row={r} />}
      toolbar={() => toolbar}
      create={{ label: 'Upload report', permission: ADJUST, render: (done, cancel) => <UploadForm done={done} cancel={cancel} /> }}
    />
  )
}
