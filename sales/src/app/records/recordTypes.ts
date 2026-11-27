import type { RecordKey } from '@/platform-ui'

// The record types with a page of their own in Sales (user 2026-11-27): the server declares each type (its list's
// service, sales-api app/platform/record_types.py); here, where its page is and the list "← Back to …" falls back
// to when nothing opened it (a typed address, a link from elsewhere).

export interface RecordTypePage {
  /** The record's address. */
  href: (key: string | number) => string
  /** Its list: where Back goes when no tab opened the record. */
  list: { path: string; title: string }
}

const enc = (k: string | number) => encodeURIComponent(String(k))

export const RECORD_TYPES: Record<string, RecordTypePage> = {
  order: { href: (id) => `/orders/${enc(id)}`, list: { path: '/orders', title: 'Orders' } },
  item: { href: (code) => `/items/${enc(code)}`, list: { path: '/config/items', title: 'Items' } },
  lot: { href: (lot) => `/lots/${enc(lot)}`, list: { path: '/lots', title: 'Lots' } },
}

export const hrefOf = (r: RecordKey): string | null => RECORD_TYPES[r.type]?.href(r.key) ?? null
