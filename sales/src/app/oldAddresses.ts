import type { ViewRequest } from '@/platform-ui'

/** Orders in review: one reason of the On hold view, opened as a view of its own (no saved view). */
export const NEEDS_REVIEW: ViewRequest = {
  name: 'Needs review', state: { filters: [{ field: 'on_hold_reason', op: 'eq', value: 'Needs Review' }] },
}

// Addresses that became a view of another list: the layout sends them there
// (ViewRedirect asks the list for the view, whether its tab is new or open).
export const OLD_ADDRESSES: Record<string, { to: string; resource: string; view: ViewRequest }> = {
  // The Review worklist became the Orders view "Needs review" (2026-10-30), then a filter of On hold (2026-11-09).
  '/review': { to: '/orders', resource: 'orders', view: NEEDS_REVIEW },
  // Manual orders became the Orders view "Manual drafts" + New manual order (2026-11-02).
  '/orders/manual': { to: '/orders', resource: 'orders', view: 'manual_drafts' },
}

/** The problem key of a Task Inbox link (/inbox/<key>), or null: InboxRedirect opens its page. */
export function inboxKey(pathname: string): string | null {
  const m = pathname.match(/^\/inbox\/(.+)$/)
  return m ? decodeURIComponent(m[1]) : null
}
