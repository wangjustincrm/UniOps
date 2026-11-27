import { z } from 'zod'
import { useState } from 'react'
import {
  ActionButton, Button, ListPage, actionIcon, useListMeta, type Filter, Callout, useRecordList, FormDialog, SchemaForm, type FieldSpec, Text, Cluster,
} from '@/platform-ui'
import { lotColumns as columns } from './lotColumns'
import { RECALL_FILE, downloadRecall, lotsClient, parseLots, shippedClient, type LotRead } from './lotsApi'

const MAX_LOTS = 100  // the list query's limit on one filter's values

/** How far a recall reaches: the orders and customers its lots went to. */
function RecallReach({ lots }: { lots: string[] }) {
  const q = useRecordList(lotsClient, { page: 1, pageSize: 1, filters: [{ field: 'lot', op: 'in', value: lots }],
    group: [{ field: 'partner_name' }, { field: 'order_ref' }] })
  if (!q.data) return null
  const groups = q.data.groups ?? []
  const customers = new Set(groups.map((g) => g.key[0])).size
  return <span>{q.data.total} rows · {groups.length} {groups.length === 1 ? 'order' : 'orders'} · {customers} {customers === 1 ? 'customer' : 'customers'}</span>
}

const recallSchema = z.object({
  lots: z.string().refine((t) => parseLots(t).length > 0, 'Paste at least one lot.')
    .refine((t) => parseLots(t).length <= MAX_LOTS, `At most ${MAX_LOTS} lots at a time.`),
})
const recallFields: FieldSpec[] = [
  { name: 'lots', label: 'Lots', type: 'textarea', required: true, placeholder: 'LOT-A\nLOT-B',
    hint: (v) => { const n = parseLots(String(v.lots ?? '')).length; return n ? `${n} ${n === 1 ? 'lot' : 'lots'}` : 'One per line, or separated by commas.' } },
]

function RecallDialog({ open, onOpenChange, onApply }: { open: boolean; onOpenChange: (o: boolean) => void; onApply: (lots: string[]) => void }) {
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Recall: find where lots went"
      description="Paste one lot or several (one per line, or separated by commas). Every order and customer they went to is listed, ready to export.">
      <SchemaForm schema={recallSchema} fields={recallFields} defaultValues={{ lots: '' }} submitLabel="Find" onCancel={() => onOpenChange(false)}
        onSubmit={(v) => { onApply(parseLots(String(v.lots))); onOpenChange(false) }} />
    </FormDialog>
  )
}

/** Operations → Lots: every pallet row a 945 reported, with the order and
 *  customer it went to — for tracing and recalls. A recall (one lot or a
 *  pasted list) lists every order and customer they reached, as CSV too.
 *  Nothing here is ever deleted (FR-OB-05). */
export default function LotsPage() {
  const [recall, setRecall] = useState<string[] | null>(null)
  const [asking, setAsking] = useState(false)
  const fixed: Filter[] | undefined = recall ? [{ field: 'lot', op: 'in', value: recall }] : undefined
  // The recall's own file (customers, orders, delivery: the applied 945s), for whom the server says may export it.
  const recallAllowed = !!useListMeta(shippedClient).data?.capabilities?.files?.find((x) => x.path === RECALL_FILE)?.allowed
  return (
    <>
      <ListPage<LotRead>
        key={recall ? recall.join('|') : 'all'}
        resource={lotsClient}
        title="Lots"
        description="Every pallet row the warehouse's 945s reported: lot, expiry, quantity, and the order and customer it went to."
        columns={columns}
        fixed={fixed}
        emptyTitle={recall ? 'None of these lots has shipped' : 'No 945 rows yet'}
        notice={recall && (
          <Callout label="Recall">
            <Cluster as="span" gap={2} align="center">
              <Text weight="medium">Recall: {recall.join(', ')}</Text>
              <span>—</span><RecallReach lots={recall} />
              {recallAllowed && (
                <ActionButton size="sm" className="ml-auto" done="Recall exported" failPrefix="Export failed" refreshes={false}
                  onAction={() => downloadRecall(recall)}>{actionIcon('export')}Export for a recall (CSV)</ActionButton>
              )}
              <Button size="sm" variant="secondary" className={recallAllowed ? undefined : 'ml-auto'} onClick={() => setRecall(null)}>Clear recall</Button>
            </Cluster>
          </Callout>
        )}
        toolbar={() => (
          <Button size="sm" variant="secondary" onClick={() => setAsking(true)}>Recall…</Button>
        )}
      />
      <RecallDialog open={asking} onOpenChange={setAsking} onApply={setRecall} />
    </>
  )
}
