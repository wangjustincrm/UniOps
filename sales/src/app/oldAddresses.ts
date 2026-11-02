// Addresses that became a view of another list: the layout sends them there
// (ViewRedirect asks the list for the view, whether its tab is new or open).
export const OLD_ADDRESSES: Record<string, { to: string; resource: string; view: string }> = {
  // The Review worklist became the Orders view "Needs review" (2026-10-30).
  '/review': { to: '/orders', resource: 'orders', view: 'needs_review' },
  // Manual orders became the Orders view "Manual drafts" + New manual order (2026-11-02).
  '/orders/manual': { to: '/orders', resource: 'orders', view: 'manual_drafts' },
}
