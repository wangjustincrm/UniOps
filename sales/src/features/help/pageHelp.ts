import type { HelpEntry } from '@/platform-ui'

// The pages' help panels and the lists' empty texts (the user's approved texts, 2026-11-17, with the
// reviewer's fixes). A screen's own words: they live here, not in the API. An empty text shows only for a
// list with nothing narrowing it ("Nothing in this view" / "No rows match the current filters" otherwise).

export const PAGE_HELP: Record<string, HelpEntry> = {
  'page:orders': {
    title: 'Orders',
    body: 'Every order: from the Shopify store, retailer 850s and manual entry, or a warehouse 945 with no order. Each order shows its Status, and on the order, whose turn it is.',
    tasks: [
      'Find an order: search its Order ref, Internal no., PO, partner or a tag.',
      'Use a view: Open, To fulfill, On hold, To invoice, To settle, Manual drafts, Manual orders by reason.',
      'Open an order to act on it: Send 940 now, Release hold, Confirm delivery.',
      'New manual order: a customer order typed in by hand, with its reason (Gift, Replacement, Sample …).',
      'A gift or replacement: on their order, More → Reorder for this customer; add lines and a reason, then submit.',
      'Export CSV: every order the list selects, with ship-to as the list shows it. Who exported what is kept in the Audit log.',
      'Manual orders by reason: gifts, replacements, samples … by month and reason, with the EA.',
      'Payment (Finance): the To settle view; tick orders → Mark settled, or an order\'s ⋯ menu.',
    ],
  },
  'page:auditLog': {
    title: 'Audit log',
    body: 'Every change made through Sales (by a person or a run) and every failed request: who, when, what, how long, the outcome. Reads that work are not kept.',
    tasks: [
      'Narrow it with When, Kind, Method and Outcome, or search a user, a route or a correlation id.',
      'Open a row: Overall, the Actions it ran (with what was sent) and the Entity changes it made.',
      'Entity changes: every change to one record — search its id.',
    ],
  },
  'page:delivery': {
    title: 'Delivery confirmation',
    body: 'Shipped orders whose delivery is due. Confirming a delivery makes its invoice.',
    tasks: [
      'Confirm one order, or Confirm all shown.',
      'Change the date if you know the real delivery day: it becomes the invoice date.',
      'If part was returned or refused, enter it per line when you confirm one order.',
    ],
  },
  'page:invoices': {
    title: 'Invoices',
    body: "One invoice per confirmed order, sent by the partner's method (810 or email with PDFs).",
    tasks: ['Check and Edit invoice before sending.', 'Send, or Send again.', 'Send several at once: the Ready to send view, tick them, Send: it says first which go, how, and which not and why.',
      'Release an invoice that is on hold.'],
  },
  'page:transfers': {
    title: 'Transfers',
    body: 'Stock sent from the plant to the warehouse (Radiant): a 943 says what is coming, its 944 says what arrived.',
    tasks: ['New transfer, then Ready to send.', 'Settle a receipt difference.', 'Resend 943 if it failed.'],
  },
  'page:inventory': {
    title: 'Inventory',
    body: "Stock at Radiant by item or by lot, summed from Sales' own records.",
    about: { title: 'How these figures are made', text: "Every figure is a sum of moves made from Sales' own records: transfers (943) and their receipts (944), shipments (945), deliveries and returns. A Radiant report applied as the start (Radiant reports) sets every lot to Radiant's figure at its moment; the moves after it add up from there." },
    tasks: ['Open an item to see its lots.', 'Switch to By lot.', 'Apply a Radiant stock report (Radiant reports).'],
  },
  'page:payments': {
    title: 'Payments',
    body: 'Every payment and refund of the orders, by method, week or month, and the exceptions.',
    about: { title: 'Where these come from', text: 'Every payment and refund transaction Shopify has for an order, read-only. Sales, Refunds and Net count successful transactions only. With an Authorize.Net connection, each Authorize.Net payment also shows the batch it was settled in and its Authorize.Net transaction ID, read from Authorize.Net and matched to the Shopify payment. A settled Authorize.Net transaction that is no Shopify payment is a row of its own (Exceptions).' },
    tasks: ['Choose a view: a method, by week or month, the exceptions.', 'Open the order of a payment.'],
  },
  'page:settlements': {
    title: 'Settlements',
    body: 'Each deposit a payment provider paid to the bank, with the payments in it.',
    about: { title: 'Where these come from', text: 'Each deposit a payment provider paid to the bank, read from the provider. Authorize.Net: one settled batch, about one a day; it reports no fees per deposit (it bills them monthly). PayPal: one transfer to the bank, with everything since the previous one; Net is after PayPal\'s fees, and what has not been transferred yet is the "In PayPal balance" row. Matched in Shopify adds up the Shopify payments matched to the deposit\'s transactions. Chargebacks and returned items are shown on their own, not in Net.' },
    tasks: ['Open a deposit to see its payments.', 'Print the report of the list as it is filtered.'],
  },
  'page:health': {
    title: 'Health',
    body: 'Checks of the settings, connections, runs, files and sends.',
    tasks: ['Open a warning to see the records behind it.', 'Check that automatic runs are on.'],
  },
}

/** Why an unfiltered list is empty and what to do. */
export const EMPTY_TEXT = {
  orders: 'Orders arrive from Shopify and from retailer 850s on each run; check Connections if none come. Or make a New manual order.',
  delivery: 'Shipped orders appear here once their delivery is due.',
  invoices: 'An invoice is made when a delivery is confirmed.',
  transfers: 'Make a New transfer to send stock to the warehouse (Radiant).',
  payments: 'They are read from Shopify with its orders.',
  settlements: 'They are read from Authorize.Net once its connection has a start date (Configuration → Connections).',
  documents: 'Every file read from or sent to a partner appears here.',
  inventory: 'Stock appears as transfers are received (944) and orders ship (945).',
  runs: 'Run now, or turn on automatic runs in Connections.',
} as const
