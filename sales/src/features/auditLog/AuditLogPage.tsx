import { useState } from 'react'
import {
  AuditLogSheet, Badge, DateRangeField, FacetFilter, Kbd, ListPage, Tabs, Text, columnsOf, type ColumnDef, type DateRangeValue, type Filter,
} from '@/platform-ui'
import { auditLogsClient, entityChangesClient, fetchAuditLog, type AuditLogRow, type EntityChangeRow } from './auditLogApi'

const col = columnsOf<AuditLogRow>()
const chg = columnsOf<EntityChangeRow>()
const KINDS = ['request', 'job', 'outbox', 'intake', 'cli'].map((value) => ({ value }))
const METHODS = ['POST', 'PUT', 'PATCH', 'DELETE', 'GET'].map((value) => ({ value }))
const OUTCOMES = [{ value: 'failed', label: 'Failed (4xx / 5xx)' }, { value: 'exception', label: 'Has an exception' }]

const logColumns: ColumnDef<AuditLogRow, unknown>[] = [
  col.datetime('execution_time', 'Time'),
  { accessorKey: 'kind', header: 'Kind', cell: ({ row: { original: r } }) => <Badge variant="neutral">{r.kind}</Badge> },
  { accessorKey: 'route', header: 'What', cell: ({ row: { original: r } }) => (
    <Text wrap="clamp2">{r.kind === 'request' ? `${r.http_method ?? ''} ${r.route ?? ''}` : r.name ?? ''}</Text>
  ) },
  col.text('user_name', 'User'),
  { accessorKey: 'http_status', header: 'Status', cell: ({ row: { original: r } }) => r.http_status == null ? ''
    : <Badge variant={r.http_status >= 500 ? 'danger' : r.http_status >= 400 ? 'warning' : 'success'}>{r.http_status}</Badge> },
  col.text('error_code', 'Error'),
  col.number('execution_duration_ms', 'Duration (ms)'),
  col.number('entity_changes', 'Changes'),
  col.text('correlation_id', 'Correlation id'),
]
const changeColumns: ColumnDef<EntityChangeRow, unknown>[] = [
  chg.datetime('at', 'Time'), chg.text('entity', 'Entity'), chg.text('entity_id', 'Id'), chg.text('action', 'Action'),
  chg.text('user_name', 'User'), chg.text('reason', 'Reason'),
]

/** The toolbar's quick filters (dates and facets, ≈ shadcn's data-table toolbar) as the list's own filters. */
function quickFilters(dates: DateRangeValue, kinds: string[], methods: string[], outcome: string[], dateField: string): Filter[] {
  const f: Filter[] = []
  if (dates.from) f.push({ field: dateField, op: 'between', value: [dates.from, `${dates.to ?? dates.from}T23:59:59`] })
  if (kinds.length) f.push({ field: 'kind', op: 'in', value: kinds })
  if (methods.length) f.push({ field: 'http_method', op: 'in', value: methods })
  if (outcome.includes('failed')) f.push({ field: 'http_status', op: 'gte', value: '400' })
  if (outcome.includes('exception')) f.push({ field: 'has_exception', op: 'eq', value: 'true' })
  return f
}

/** Operations → Audit log (≈ ABP Pro): every saved request and background scope — who, what, when, how long, the
 *  outcome — opened with its actions and entity changes; and every entity change by entity. sales.config for now. */
export default function AuditLogPage() {
  const [dates, setDates] = useState<DateRangeValue>({ from: null, to: null })
  const [kinds, setKinds] = useState<string[]>([])
  const [methods, setMethods] = useState<string[]>([])
  const [outcome, setOutcome] = useState<string[]>([])
  const [opened, setOpened] = useState<string | null>(null)
  const toolbar = (
    <>
      <DateRangeField value={dates} onChange={setDates} label="When" />
      <FacetFilter title="Kind" options={KINDS} selected={kinds} onChange={setKinds} />
      <FacetFilter title="Method" options={METHODS} selected={methods} onChange={setMethods} />
      <FacetFilter title="Outcome" options={OUTCOMES} selected={outcome} onChange={setOutcome} />
    </>
  )
  const filters = quickFilters(dates, kinds, methods, outcome, 'execution_time')
  return (
    <>
      <Tabs ariaLabel="Audit log" defaultValue="logs" items={[
        { value: 'logs', label: 'Requests and jobs', content: (
          <ListPage<AuditLogRow> key={JSON.stringify(filters)} resource={auditLogsClient} title="Audit log" columns={logColumns}
            description="Every change made through Sales and every failed request, with who, when and what. Reads that work are not kept. Open one for its actions and the records it changed."
            fixed={filters.length ? filters : undefined} toolbar={() => toolbar} onRowClick={(r) => setOpened(r.uuid)}
            emptyTitle="Nothing recorded yet" help="page:auditLog" />
        ) },
        { value: 'changes', label: 'Entity changes', content: (
          <ListPage<EntityChangeRow> resource={entityChangesClient} title="Entity changes" columns={changeColumns}
            description="Every change to a record, by entity and id (search an Order id, a transfer…). Open a row for its request."
            onRowClick={(r) => r.audit_log_id && setOpened(r.audit_log_id)} emptyTitle="No change recorded yet" />
        ) },
      ]} />
      <Text as="p" size="xs" tone="soft" className="mt-2">In an open log, <Kbd>Esc</Kbd> closes it.</Text>
      <AuditLogSheet id={opened} load={fetchAuditLog} open={opened !== null} onOpenChange={(o) => { if (!o) setOpened(null) }} />
    </>
  )
}
