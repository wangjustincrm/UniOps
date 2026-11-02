import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, ConfirmAction, DateTimeCell, DiffTable, ErrorView, ListPage, Skeleton, StatusCell, queryKeys,
  type ColumnDef,
} from '@/platform-ui'
import { SHADOW_STATUSES, fetchShadowCompare, fetchShadowSummary, markExpected, shadowClient, type ShadowMatchRead } from './shadowApi'

const SUMMARY_KEY = ['sales', 'shadow-matches', 'summary']

const columns: ColumnDef<ShadowMatchRead, unknown>[] = [
  { accessorKey: 'kind', header: 'Kind' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'reference_number', header: 'Order no. / PO' },
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'differences', header: 'Differences' },
  { accessorKey: 'note', header: 'Note', cell: ({ row }) => (
    <span className="line-clamp-1 text-neutral-500">{row.original.expected_reason ?? row.original.note ?? ''}</span>
  ) },
  { accessorKey: 'compared_at', header: 'Compared', cell: ({ row }) => <DateTimeCell value={row.original.compared_at} /> },
]

function Summary() {
  const q = useQuery({ queryKey: SUMMARY_KEY, queryFn: fetchShadowSummary })
  if (!q.data) return null
  return (
    <ul aria-label="Comparison summary" className="flex flex-wrap gap-3 text-sm">
      {SHADOW_STATUSES.map((s) => (
        <li key={s} className="flex items-center gap-1"><StatusCell value={s} /><span className="font-semibold">{q.data[s] ?? 0}</span></li>
      ))}
    </ul>
  )
}

const errorMessages: Record<string, string> = {
  'shadow.not_differs': 'Only a comparison that differs can be marked.',
  'entity.stale': 'This comparison changed meanwhile. Close it and open it again.',
  'auth.forbidden': 'You do not have permission to mark differences.',
}

function MarkExpected({ m, onDone }: { m: ShadowMatchRead; onDone: () => void }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  return (
    <>
      <ActionButton size="sm" variant="secondary" permission="sales.order.operate" onAction={() => setOpen(true)}>Mark as expected difference</ActionButton>
      <ConfirmAction open={open} onOpenChange={setOpen} title="Mark these differences as expected?"
        description="Kept while the differences stay the same; a new difference sets it back to Differs."
        confirmLabel="Mark as expected" reason={{ label: 'Why these differences are expected' }} errorMessages={errorMessages}
        onConfirm={async (why) => {
          await markExpected(m.id, why, m.version)
          await qc.invalidateQueries({ queryKey: queryKeys.list(shadowClient) })
          await qc.invalidateQueries({ queryKey: SUMMARY_KEY })
          onDone()
        }} />
    </>
  )
}

/** Both files side by side; differing segments highlighted (§5.4). */
function Detail({ id, close }: { id: number; close: () => void }) {
  const q = useQuery({ queryKey: queryKeys.detail(shadowClient, id), queryFn: () => fetchShadowCompare(id) })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const m = q.data
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p>
        <StatusCell value={m.status} /> · {m.reference_name ? `Zoho file ${m.reference_name}` : 'no Zoho file'}
        {m.order_id != null && <> · Sales order {m.order_id}</>}
      </p>
      {m.document_name && <p>Warehouse file {m.document_name} ({m.kind}) — compared, never applied.</p>}
      {(m.note || m.expected_reason) && <p className="text-neutral-600">{m.expected_reason ?? m.note}</p>}
      {m.would && (
        <section aria-label="What Sales would have recorded">
          <h3 className="mb-1 font-semibold text-neutral-800">What Sales would have recorded</h3>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-2 text-xs">{JSON.stringify(m.would, null, 2)}</pre>
        </section>
      )}
      {m.status === 'Differs' && <MarkExpected m={m} onDone={close} />}
      {m.rows.length > 0 && (
        <DiffTable ariaLabel="Segments" leftLabel="Zoho sent" rightLabel="Sales would send (dry run)"
          rows={m.rows.map((r) => ({ left: r.zoho, right: r.sales, same: r.same }))} />
      )}
      {m.ignored.length > 0 && (
        <details className="text-xs text-neutral-500">
          <summary>Ignored when comparing</summary>
          <ul>{m.ignored.map((x) => <li key={x}>{x}</li>)}</ul>
        </details>
      )}
    </div>
  )
}

/** Operations → Shadow compare: each 940 Zoho sent against the 940 Sales would
 *  send for the same order, during the shadow run (design §5.4). */
export default function ShadowComparePage() {
  return (
    <ListPage<ShadowMatchRead>
      resource={shadowClient}
      title="Shadow compare"
      description="Each 940 Zoho sent (from the reference folder) against the 940 Sales would send for the same order, and each 997 / 945 the warehouse returned for Zoho's 940s with what Sales would have recorded. Nothing here is sent or applied; Run now refreshes it."
      columns={columns}
      emptyTitle="Nothing compared yet"
      notice={<Summary />}
      detailTitle={(m) => `Comparison ${m.reference_number ?? m.id}`}
      detailClassName="max-w-5xl"
      renderDetail={(m, close) => <Detail id={m.id} close={close} />}
    />
  )
}
