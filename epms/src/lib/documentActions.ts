/**
 * Who sees Withdraw (cancel) and Recall on the PO / PA / Agreement / GR detail
 * pages.
 *
 * The server is the authority; these predicates only decide whether a button is
 * offered, and they must never offer one the server would refuse. They mirror
 * approval-api app/crud/engine.py::execute_action:
 *   - cancel: status in _DOC_META[doc]["valid_cancel"]; the creator, a System
 *     Admin (JWT main role), or the approver currently holding the document.
 *   - recall: status in RECALLABLE_STATUSES; the creator or a System Admin.
 * and, for GRs, epms-api app/crud/gr.py::action ("cancel").
 */

/** approval-api _DOC_META[...]["valid_cancel"] for the documents with these pages. */
export const CANCELLABLE_STATUSES = {
  po: ['draft', 'returned', 'submitted'],
  pa: ['draft', 'returned'],
  agr: ['draft', 'returned', 'submitted'],
} as const

/** approval-api engine.py RECALLABLE_STATUSES (every document type). */
export const RECALLABLE_STATUSES = ['submitted', 'in_review'] as const

/** epms-api crud/gr.py action "cancel" (PRD GR-C-004). */
export const GR_CANCELLABLE_STATUSES = ['pending_ack', 'collection_pending'] as const

export interface Actor {
  /** JWT main role — the engine's System Admin bypass reads only this. */
  role?: string | null
  id?: string | null
}

const isAdmin = (actor: Actor) => actor.role === 'system_admin'
const isCreator = (actor: Actor, createdBy?: string | null) =>
  !!actor.id && !!createdBy && actor.id === createdBy

/**
 * Withdraw (action "cancel") on a PO, PA or agreement.
 *
 * `holdsApproveTask` is the page's existing open-approve-task check — the
 * engine's "approver currently holding it" arm. Only a submitted document has
 * such a holder, so a returned or draft one is offered to its creator and
 * System Admin only.
 */
export function canWithdrawDocument(
  docType: keyof typeof CANCELLABLE_STATUSES,
  doc: { status: string; created_by?: string | null },
  actor: Actor,
  holdsApproveTask = false,
): boolean {
  if (!(CANCELLABLE_STATUSES[docType] as readonly string[]).includes(doc.status)) return false
  return isAdmin(actor) || isCreator(actor, doc.created_by) || holdsApproveTask
}

/** Recall (action "recall") back to draft: the creator or a System Admin. */
export function canRecallDocument(
  doc: { status: string; created_by?: string | null },
  actor: Actor,
): boolean {
  if (!(RECALLABLE_STATUSES as readonly string[]).includes(doc.status)) return false
  return isAdmin(actor) || isCreator(actor, doc.created_by)
}

/**
 * Cancel on a goods receipt.
 *
 * The GR action endpoint itself checks no permission for cancel, so this is
 * deliberately narrower than the server: the person who recorded the receipt,
 * a warehouse receiver (`epms.gr.receive`, the permission that records a GR)
 * or a System Admin.
 */
export function canCancelGr(
  gr: { status: string; created_by?: string | null },
  actor: Actor,
  canReceive: boolean,
): boolean {
  if (!(GR_CANCELLABLE_STATUSES as readonly string[]).includes(gr.status)) return false
  return isAdmin(actor) || isCreator(actor, gr.created_by) || canReceive
}
