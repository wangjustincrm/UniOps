import { useState } from 'react'
import { PartnerCell } from '@/features/common/PartnerCell'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, ConfirmAction, DateTimeCell, DetailList, DiffTable, ListPage, RecordSheet, Section, StatusCell, queryKeys,
  type ColumnDef, type RecordTab,
} from '@/platform-ui'
import { SHADOW_STATUSES, fetchShadowCompare, fetchShadowSummary, markExpected, shadowClient, type ShadowMatchRead } from './shadowApi'

const SUMMARY_KEY = ['sales', 'shadow-matches', 'summary']

const columns: ColumnDef<ShadowMatchRead, unknown>[] = [
  { accessorKey: 'kind', header: 'Kind' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'reference_number', header: 'Zoho order no. / PO' },
  { accessorKey: 'order_ref', header: 'Order ref', cell: ({ row }) => row.original.order_ref ?? '' },
  { accessorKey: 'order_number', header: 'Internal no.', cell: ({ row }) => row.original.order_number ?? '' },
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
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
function Detail({ row, close }: { row: ShadowMatchRead; close: () => void }) {
  const q = useQuery({ queryKey: queryKeys.detail(shadowClient, row.id), queryFn: () => fetchShadowCompare(row.id) })
  const m = q.data
  const differing = m ? m.rows.filter((r) => !r.same).length : 0
  const tabs: RecordTab[] = !m ? [] : [
    { id: 'overview', label: 'Overview', render: () => (
      <Section title="Comparison">
        <DetailList items={[
          ['Zoho file', m.reference_name ?? 'no Zoho file'],
          ['Warehouse file', m.document_name ? `${m.document_name} (${m.kind}) — compared, never applied` : null],
          ['Order ref', row.order_ref], ['Internal no.', row.order_number == null ? null : String(row.order_number)],
          ['Zoho order no. / PO', row.reference_number],
          ['Note', m.expected_reason ?? m.note],
        ]} />
        {m.ignored.length > 0 && (
          <details className="text-xs text-neutral-500">
            <summary>Ignored when comparing</summary>
            <ul>{m.ignored.map((x) => <li key={x}>{x}</li>)}</ul>
          </details>
        )}
      </Section>
    ) },
    { id: 'segments', label: 'Segments', count: differing || undefined, tone: differing ? 'warning' : undefined, hidden: m.rows.length === 0,
      render: () => (
        <DiffTable ariaLabel="Segments" leftLabel="Zoho sent" rightLabel="Sales would send (dry run)"
          rows={m.rows.map((r) => ({ left: r.zoho, right: r.sales, same: r.same }))} />
      ) },
    { id: 'would', label: 'What Sales would record', hidden: !m.would, render: () => (
      <pre aria-label="What Sales would have recorded" className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs">{JSON.stringify(m.would, null, 2)}</pre>
    ) },
  ]
  return (
    <RecordSheet
      title={row.order_ref ?? row.reference_number ?? `Comparison ${row.id}`}
      subtitle={[row.kind, row.order_number != null && `internal no. ${row.order_number}`, row.reference_number && `Zoho ${row.reference_number}`,
        row.partner_name].filter(Boolean).join(' · ')}
      badges={<StatusCell value={m?.status ?? row.status} />}
      size="xl" loading={q.isLoading} error={q.error}
      onRefresh={() => q.refetch()} updatedAt={q.dataUpdatedAt} fetching={q.isFetching} snapshot={m as unknown as Record<string, unknown> | undefined}
      facts={m ? [
        { label: 'Status', value: m.status, tone: m.status === 'Match' || m.status === 'Expected difference' ? 'success' : 'warning' },
        { label: 'Differences', value: differing, tone: differing ? 'warning' : 'neutral' },
        { label: 'Segments', value: m.rows.length },
        { label: 'Kind', value: m.kind },
      ] : undefined}
      actions={m?.status === 'Differs' ? <MarkExpected m={m} onDone={close} /> : undefined}
      tabs={tabs}
    />
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
      renderDetail={(m, close) => <Detail row={m} close={close} />}
    />
  )
}
