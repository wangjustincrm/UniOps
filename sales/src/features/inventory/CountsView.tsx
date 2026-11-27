import { Upload } from 'lucide-react'
import { z } from 'zod'
import { type ReactNode } from 'react'
import { ListPage, columnsOf, type ColumnDef, SchemaForm, type FieldSpec } from '@/platform-ui'
import { stockCountsClient, uploadCount, type StockCountRead } from './inventoryApi'
import { ADJUST, errorMessages } from './countRules'
import { CountSheet } from './CountSheet'

const MAX_MB = 20
const colCount = columnsOf<StockCountRead>()
const countColumns: ColumnDef<StockCountRead, unknown>[] = [
  colCount.datetime('counted_at', 'Counted at'),
  { id: 'state', header: 'State', cell: ({ row }) => (row.original.applied_at ? 'Applied: the start' : 'Report only') },
  colCount.text('file_name', 'File'),
  colCount.text('note', 'Note'),
  colCount.text('created_by_name', 'Uploaded by'),
  colCount.datetime('applied_at', 'Applied'),
]
const uploadSchema = z.object({
  file: z.instanceof(File, { message: 'Choose the report file' }),
  counted_at: z.string().min(1, 'Set when the report was taken.'),
  note: z.string().trim().max(2000).nullable().optional(),
})
const uploadFields: FieldSpec[] = [
  { name: 'about', label: '', type: 'note', note: () => "The inventory report from Radiant's portal (CSV or Excel), by pallet or by lot. Nothing changes until you apply it." },
  { name: 'file', label: 'Report file', type: 'file', required: true, accept: '.csv,.xlsx,.txt', maxBytes: MAX_MB * 1024 * 1024 },
  { name: 'counted_at', label: 'Taken at', type: 'datetime', required: true, hint: 'When the report was taken from the portal: moves after it are not in it.' },
  { name: 'note', label: 'Note', type: 'textarea' },
]

function UploadForm({ done, cancel }: { done: (c?: StockCountRead) => void; cancel: () => void }) {
  return (
    <SchemaForm schema={uploadSchema} fields={uploadFields} defaultValues={{ file: null, counted_at: '', note: '' }} submitLabel="Upload"
      errorMessages={errorMessages} onCancel={cancel} done="Uploaded" after={(c) => done(c as StockCountRead)}
      onSubmit={(v) => uploadCount(v.file as File, String(v.counted_at), (v.note as string | null)?.trim() || null)} />
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
      create={{ label: 'Upload report', permission: ADJUST, icon: <Upload aria-hidden className="mr-1 h-4 w-4" />, render: (done, cancel) => <UploadForm done={done} cancel={cancel} /> }}
    />
  )
}
