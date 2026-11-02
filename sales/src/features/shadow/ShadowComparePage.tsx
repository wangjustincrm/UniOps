import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, DateTimeCell, ErrorMessage, ErrorView, FormField, ListPage, Skeleton, StatusCell, Textarea, cn, queryKeys,
  type ColumnDef,
} from '@/platform-ui'
import { SHADOW_STATUSES, fetchShadowCompare, fetchShadowSummary, markExpected, shadowClient, type ShadowMatchRead } from './shadowApi'

const SUMMARY_KEY = ['sales', 'shadow-matches', 'summary']

const columns: ColumnDef<ShadowMatchRead, unknown>[] = [
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
  const [reason, setReason] = useState('')
  const mut = useMutation({
    mutationFn: () => markExpected(m.id, reason.trim(), m.version),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.list(shadowClient) })
      await qc.invalidateQueries({ queryKey: SUMMARY_KEY })
      onDone()
    },
  })
  return (
    <section aria-label="Expected difference" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <FormField label="Why these differences are expected" htmlFor="shadow-reason" hint="Kept while the differences stay the same; a new difference sets it back to Differs.">
        <Textarea id="shadow-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </FormField>
      <ActionButton size="sm" permission="sales.order.operate" disabled={reason.trim().length < 3}
        onAction={() => mut.mutateAsync().catch(() => {})}>Mark as expected difference</ActionButton>
      <ErrorMessage error={mut.error} messages={errorMessages} />
    </section>
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
      {(m.note || m.expected_reason) && <p className="text-neutral-600">{m.expected_reason ?? m.note}</p>}
      {m.status === 'Differs' && <MarkExpected m={m} onDone={close} />}
      {m.rows.length > 0 && (
        <table aria-label="Segments" className="w-full table-fixed font-mono text-xs">
          <thead><tr className="text-left text-neutral-500"><th className="w-1/2">Zoho sent</th><th className="w-1/2">Sales would send (dry run)</th></tr></thead>
          <tbody>
            {m.rows.map((r, i) => (
              <tr key={i} data-differs={!r.same || undefined} className={cn('align-top', !r.same && 'bg-warning-50 text-warning-700')}>
                <td className="break-all py-0.5 pr-2">{r.zoho ?? '—'}</td>
                <td className="break-all py-0.5">{r.sales ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <details className="text-xs text-neutral-500">
        <summary>Ignored when comparing</summary>
        <ul>{m.ignored.map((x) => <li key={x}>{x}</li>)}</ul>
      </details>
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
      description="Each 940 Zoho sent (from the reference folder) against the 940 Sales would send for the same order. Nothing here is sent; Run now refreshes it."
      columns={columns}
      emptyTitle="Nothing compared yet"
      notice={<Summary />}
      detailTitle={(m) => `Comparison ${m.reference_number ?? m.id}`}
      detailClassName="max-w-5xl"
      renderDetail={(m, close) => <Detail id={m.id} close={close} />}
    />
  )
}
