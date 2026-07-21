import { financeHandoffHref } from './api'

// ─── Task types ────────────────────────────────────────────────────────────────
//
// Single source of truth for task-type display labels and navigation. The backend
// engine emits exactly the task types listed in ALL_TASK_TYPES — keep this file in
// lockstep with it so the Task Inbox page, the dashboard mini-inbox, and any filter
// dropdowns all agree on labels and routes.

export const ALL_TASK_TYPES = [
  'create_pr',
  'create_po',
  'create_pa',
  'create_prepayment_pa',
  'approve_pr',
  'approve_po',
  'approve_pa',
  'place_order',
  'confirm_settlement',
  'review_match',
  'match_invoice',
] as const

export type TaskType = (typeof ALL_TASK_TYPES)[number]

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  create_pr: 'Create Purchase Request',
  create_po: 'Create Purchase Order',
  create_pa: 'Create Payment Application',
  create_prepayment_pa: 'Create Prepayment PA',
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  place_order: 'Place Order',
  confirm_settlement: 'Confirm Settlement',
  review_match: 'Review Invoice Match',
  match_invoice: 'Match Invoice to PO',
}

// ─── Navigation ────────────────────────────────────────────────────────────────

// document_type → detail-page route prefix.
const HREF_MAP: Record<string, string> = {
  pr: '/pr',
  po: '/po',
  gr: '/gr',
  invoice: '/invoices',
  pa: '/pa',
}

// A task carries a type plus the document it anchors on. Accept the minimal shape
// so this resolver works with the raw ApiTask as well as any mapped view model.
export interface TaskHrefInput {
  type: string
  document_type: string
  document_id: string
}

/**
 * Resolve the destination for a task card. Replaces the divergent per-surface
 * logic that previously lived in the inbox page and the dashboard.
 */
export function taskHref(task: TaskHrefInput): string {
  const docType = task.document_type.toLowerCase()

  // Budget Plans live in the Finance module — open them there via a full-page
  // handoff (absolute URL), not an in-app EPMS route.
  if (docType === 'budget_plan') {
    return financeHandoffHref(`/budget/plans/${task.document_id}`)
  }

  // Both create_pa and create_prepayment_pa anchor on the PO and open the PA
  // create page pre-filled from it (the /pa/create and /pa/new routes are the
  // same PaCreatePage).
  if (task.type === 'create_pa' || task.type === 'create_prepayment_pa') {
    return `/pa/create?poId=${task.document_id}`
  }

  return `${HREF_MAP[docType] ?? '/'}/${task.document_id}`
}
