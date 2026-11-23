import { useState } from 'react'
import { z } from 'zod'
import {
  ActionButton, Badge, ConfirmAction, FormDialog, HelpTip, SchemaForm, formatDate, queryKeys, todayDate,
  type ActionDone, type FieldSpec,
} from '@/platform-ui'
import { ordersClient, settleBulk, settleOrder, unsettleOrder, type OrderRead, type SettleBody, type SettleResult } from './ordersApi'

// Payment (user 2026-11-23): beside the Status, whether the money came. Settled is a finance user's mark
// (sales.settle) for every source — retailer, manual, Shopify; everyone else sees it and no action.
export const SETTLE = 'sales.settle'
const OPEN = ['Not settled', 'Due', 'Overdue']
const VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  Settled: 'success', Due: 'warning', Overdue: 'danger', 'Not settled': 'neutral', 'Not needed': 'neutral',
}
const errorMessages: Record<string, string> = {
  'order.settled': 'This order is already settled: reload it.',
  'order.not_settled': 'This order is not settled any more: reload it.',
  'order.settled_future': 'The settled date cannot be in the future.',
  'order.draft': 'A draft is not an order yet: submit it first.',
  'entity.stale': 'Someone else changed this order meanwhile: it is reloaded, check it and decide again.',
  'auth.forbidden': 'Only Finance (sales.settle) marks an order settled.',
}

/** The Payment badge; a tap says what it means (the API's payment help). */
export function PaymentBadge({ o }: { o: Pick<OrderRead, 'payment_status'> }) {
  if (!o.payment_status) return null
  return (
    <HelpTip id={`payment:${o.payment_status}`} label={`What "${o.payment_status}" means`}>
      <Badge variant={VARIANT[o.payment_status] ?? 'neutral'}>{o.payment_status}</Badge>
    </HelpTip>
  )
}

const schema = z.object({
  settled_on: z.string().min(1, 'Required'),
  reference: z.string().trim().min(1, 'Required').max(100, 'At most 100 characters'),
  amount: z.number().min(0, 'Not below 0').nullable().optional(),
  note: z.string().max(2000).nullable().optional(),
})
const fields: FieldSpec[] = [
  { name: 'settled_on', label: 'Settled on', type: 'date', required: true, hint: 'The day the money came.' },
  { name: 'reference', label: 'Reference', required: true, hint: 'Bank, cheque or remittance no.' },
  { name: 'amount', label: 'Amount (optional)', type: 'number' },
  { name: 'note', label: 'Note (optional)', type: 'textarea' },
]
const body = (v: Record<string, unknown>): SettleBody => ({
  settled_on: v.settled_on as string, reference: v.reference as string,
  amount: v.amount == null || v.amount === '' ? null : String(v.amount), note: (v.note as string) || null,
})
const defaults = () => ({ settled_on: todayDate(), reference: '', amount: null, note: '' })

/** Mark settled / Undo settled on one order (its action bar). */
export function SettleActions({ o }: { o: OrderRead }) {
  const [marking, setMarking] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const name = o.source_reference ?? o.order_number
  const keys = [queryKeys.detail(ordersClient, o.id), queryKeys.list(ordersClient)]
  if (!o.payment_status || o.shadow) return null
  return (
    <>
      {o.settled_on ? (
        <ActionButton size="sm" variant="secondary" permission={SETTLE} onAction={() => setUndoing(true)}>Undo settled</ActionButton>
      ) : (
        <ActionButton size="sm" variant="secondary" permission={SETTLE} onAction={() => setMarking(true)}>Mark settled</ActionButton>
      )}
      <FormDialog open={marking} onOpenChange={setMarking} title={`Mark ${name} settled`}
        description="The money for this order came. This is Finance's record in Sales only: nothing is sent to anyone.">
        <SchemaForm schema={schema} fields={fields} defaultValues={defaults()} submitLabel="Mark settled" errorMessages={errorMessages}
          onCancel={() => setMarking(false)} done={`Order ${name} marked settled.`} invalidates={keys} after={() => setMarking(false)}
          onSubmit={(v) => settleOrder(o.id, o.version, body(v))} />
      </FormDialog>
      <ConfirmAction open={undoing} onOpenChange={setUndoing} title={`Undo settled on ${name}?`} destructive
        description={`It was settled on ${o.settled_on ? formatDate(o.settled_on) : ''} (${o.settled_reference ?? ''}). It goes back to not settled; the undo and its reason are kept in Activity.`}
        confirmLabel="Undo settled" reason={{ label: 'Why it is undone' }} errorMessages={errorMessages}
        done={{ key: `unsettle:${o.id}`, tone: 'success', text: `Order ${name}: settlement undone.` }} invalidates={keys}
        onConfirm={(why) => unsettleOrder(o.id, o.version, why)} />
    </>
  )
}

/** Orders toolbar: Mark settled for every not-settled order shown (as Confirm all shown: one result per order). */
export function SettleShown({ rows, refresh }: { rows: OrderRead[]; refresh: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const open_ = rows.filter((r) => OPEN.includes(r.payment_status ?? '') && !r.shadow)
  const settled = {
    done: (res: unknown): ActionDone => {
      const r = res as SettleResult[]
      const failed = r.filter((x) => !x.ok)
      return { key: 'settle', tone: failed.length ? 'warning' : 'success',
        text: `${r.length - failed.length} marked settled${failed.length ? `, ${failed.length} not: ${failed.map((f) => `#${f.order_id} ${f.error}`).join('; ')}` : ''}` }
    },
    after: async () => { setOpen(false); await refresh() },
  }
  return (
    <>
      <ActionButton size="sm" variant="secondary" permission={SETTLE} disabled={open_.length === 0}
        disabledReason="No order shown is waiting to be settled." onAction={() => setOpen(true)}>
        Mark shown settled ({open_.length})…
      </ActionButton>
      <FormDialog open={open} onOpenChange={setOpen} title={`Mark ${open_.length} orders settled`}
        description="Every order shown that is not settled yet, with this date and reference (e.g. one remittance for several invoices). Narrow the list first with a view, a filter or a search.">
        <SchemaForm schema={schema} fields={fields} defaultValues={defaults()} submitLabel="Mark settled" errorMessages={errorMessages}
          onCancel={() => setOpen(false)} {...settled} onSubmit={(v) => settleBulk(open_.map((r) => r.id), body(v))} />
      </FormDialog>
    </>
  )
}
