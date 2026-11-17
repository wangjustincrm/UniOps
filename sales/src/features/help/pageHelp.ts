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
      'Use a view: Open, To fulfill, On hold, To invoice, Manual drafts.',
      'Open an order to act on it: Send 940 now, Release hold, Confirm delivery.',
      'New manual order: a customer order typed in by hand.',
    ],
    // The pilot tour (the user, 2026-11-17): data only; each stop is a tool or a badge that marks itself
    // and says its own text. Opt-in from the panel's "Show me", never automatic.
    tour: [{ anchor: 'list.views' }, { anchor: 'list.search' }, { anchor: 'list.searchPanel' }, { anchor: 'status:*' }, { anchor: 'list.refresh' }],
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
    tasks: ['Check and Edit invoice before sending.', 'Send, or Send again.', 'Release an invoice that is on hold.'],
  },
  'page:transfers': {
    title: 'Transfers',
    body: 'Stock sent from the plant to the warehouse (Radiant): a 943 says what is coming, its 944 says what arrived.',
    tasks: ['New transfer, then Ready to send.', 'Settle a receipt difference.', 'Resend 943 if it failed.'],
  },
  'page:inventory': {
    title: 'Inventory',
    body: "Stock at Radiant by item or by lot, summed from Sales' own records.",
    tasks: ['Open an item to see its lots.', 'Switch to By lot.', 'Apply a Radiant stock report (Radiant reports).'],
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
  documents: 'Every file read from or sent to a partner appears here.',
  inventory: 'Stock appears as transfers are received (944) and orders ship (945).',
  runs: 'Run now, or turn on automatic runs in Connections.',
} as const
