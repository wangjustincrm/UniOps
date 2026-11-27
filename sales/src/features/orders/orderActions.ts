import { z } from 'zod'
import { defineActions, todayDate, type ActionHost, type FieldSpec } from '@/platform-ui'
import { ordersClient, type OrderRead } from './ordersApi'

// The order's record actions (user 2026-11-23): defined once; the row "⋯" menu, the selection bar in Orders and the
// order sheet render them. The server (sales-api ORDER_ACTIONS) holds the rules: eligible() is a hint for the counts.
export const SETTLE = 'sales.settle'
const NOT_NEEDED = 'Not needed'

const settleInput = {
  schema: z.object({
    settled_on: z.string().min(1, 'Required'),
    reference: z.string().trim().min(1, 'Required').max(100, 'At most 100 characters'),
    amount: z.number().min(0, 'Not below 0').nullable().optional(),
    note: z.string().max(2000).nullable().optional(),
  }),
  fields: [
    { name: 'settled_on', label: 'Settled on', type: 'date', required: true, hint: 'The day the money came.' },
    { name: 'reference', label: 'Reference', required: true, hint: 'Bank, cheque or remittance no.' },
    { name: 'amount', label: 'Amount (optional)', type: 'number', hint: 'In bulk: the same on each order; usually left empty.' },
    { name: 'note', label: 'Note (optional)', type: 'textarea' },
  ] as FieldSpec[],
  defaults: () => ({ settled_on: todayDate(), reference: '', amount: null, note: '' }),
  toBody: (v: Record<string, unknown>) => ({
    settled_on: v.settled_on, reference: v.reference,
    amount: v.amount == null || v.amount === '' ? null : String(v.amount), note: (v.note as string) || null,
  }),
}

export const ORDER_ACTIONS = defineActions<OrderRead>([
  {
    // One order: its own button (Recheck), in the row and the sheet. Many: the selected ones (user 2026-11-24).
    id: 'recheck', label: 'Re-check', permission: 'sales.order.operate', scope: 'group', placement: ['bulk'],
    eligible: (o) => (o.fulfillment_status === 'Review' ? true : 'Not in review'),
    title: (n) => `Re-check ${n === 1 ? 'this order' : `${n} orders`}`,
    description: 'Each order in review is checked again against today\'s Item Master. Nothing is sent to anyone.',
    will: (n) => `${n} will be re-checked`, done: (n) => `${n} re-checked`,
  },
  {
    // Mark settled / Undo settled: only the one for the order's state shows (not fitting: hidden, not greyed).
    id: 'settle', label: 'Mark settled', permission: SETTLE, scope: 'group', placement: ['row', 'bulk', 'header'], primary: 40, whenNotFit: 'hidden',
    eligible: (o) => (o.shadow ? 'A test-run order'
      : o.settled_on ? `Already settled on ${o.settled_on}`
        : o.auto_settlement_id != null ? `Already settled by ${o.auto_settlement_label}`
        : !o.payment_status ? 'A draft is not an order yet'
          : o.payment_status === NOT_NEEDED ? 'No payment is expected (Not needed)' : true),
    input: settleInput, help: 'term:Payment',
    title: (n) => (n === 1 ? 'Mark settled' : `Mark ${n} orders settled`),
    description: 'The money came. Finance\'s record in Sales only: nothing is sent to anyone.',
    will: (n) => `${n} will be marked settled`, done: (n) => `${n} marked settled`,
  },
  {
    id: 'unsettle', label: 'Undo settled…', permission: SETTLE, scope: 'group', placement: ['row', 'bulk', 'header'], tone: 'destructive', whenNotFit: 'hidden',
    eligible: (o) => (o.settled_on ? true : o.auto_settlement_id != null ? 'Settled by the gateway, not by hand: nothing to undo' : 'Not settled'),
    input: { reason: { label: 'Why it is undone' } },
    title: (n) => (n === 1 ? 'Undo settled' : `Undo settled on ${n} orders`),
    description: 'Back to not settled; the undo and its reason are kept in each order\'s Activity.',
    will: (n) => `${n} will go back to not settled`, done: (n) => `${n} settlement${n === 1 ? '' : 's'} undone`,
  },
])

/** Delivery confirmation: the selected orders (user 2026-11-24), each on its due date or on one date. Sending an invoice
 *  is asked for one order, or with Confirm all shown, as before: not here. */
export const DELIVERY_ACTIONS = defineActions<OrderRead>([
  {
    id: 'confirm_delivery', label: 'Confirm delivery', permission: 'sales.order.operate', scope: 'group', placement: ['bulk'],
    eligible: (o) => (o.shadow ? 'A test-run order' : o.invoice_status === 'Waiting for Delivery Confirmation' ? true : `Its invoice is ${o.invoice_status}`),
    input: {
      schema: z.object({ date: z.string().nullable().optional() }),
      fields: [{ name: 'date', label: 'Delivered on (optional)', type: 'date', hint: "Empty: each order's Delivery due date. It becomes the invoice date." }] as FieldSpec[],
      defaults: () => ({ date: '' }),
      toBody: (v: Record<string, unknown>) => ({ confirmed_date: (v.date as string) || null }),
    },
    title: (n) => (n === 1 ? 'Confirm this delivery' : `Confirm ${n} deliveries`),
    description: 'Each delivered in full: its shipped quantity, less what is recorded as returned. Nothing is sent.',
    will: (n) => `${n} will be confirmed`, done: (n) => `${n} confirmed`,
  },
])

export const orderName = (o: OrderRead) => o.source_reference ?? `#${o.order_number}`
/** For an order sheet opened outside a list. */
export const ORDER_ACTION_HOST: ActionHost<OrderRead> = { run: ordersClient.action, name: orderName }
