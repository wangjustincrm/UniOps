// Who may raise a PA on whose PO — PRD §3.15.2 "Who can create", enforced by
// epms-api pa.py create_pa (+ _may_create_pa_on_behalf), which this MUST stay
// in step with. A user whose PRIMARY role is Requester may only pay POs raised
// from their own PRs — even if a special role assignment widened their PO
// scope — UNLESS they also hold a role that pays on someone else's behalf:
//
//   procurement_officer — any PO, company-wide.
//   dept_admin          — their own department's requisitions. Dropping the
//     own-requisition filter is right for them because the PO list the API
//     returns to a dept_admin is ALREADY department-scoped server-side
//     (access_scope.visible_po_subquery via visible_pr_subquery's dept branch),
//     so what is left is what the backend accepts. The one residual gap is a PO
//     pulled in by the scope's task-chain OR-condition from another department;
//     that one still 403s on submit rather than silently paying.
//   erp_pa_officer      — NC-imported POs that have no PR only, so it is a
//     per-PO exemption (scopedRequesterMayPay below), not a role-wide one.
//
// Shared by New PA's PO picker and PA Detail's Settle (ISS-0158), so the
// button is offered exactly where the picker would accept the PO.
const ON_BEHALF_ROLES = ['procurement_officer', 'dept_admin']

/** True when the caller may only pay POs from PRs they raised. */
export function isPaRequesterScoped(
  primaryRole: string | undefined,
  roles: string[] | undefined,
): boolean {
  return primaryRole === 'requester' && !roles?.some((r) => ON_BEHALF_ROLES.includes(r))
}

/** For a requester-scoped caller: may they pay this PO? Their own PR's PO, or —
 *  holding ERP PA Officer — an NC-imported PO with no PR (pa.py
 *  _may_create_pa_on_behalf). */
export function scopedRequesterMayPay(
  po: { pr_requester_id?: string | null; pr_id?: string | null; source?: string | null },
  userId: string | undefined,
  roles: string[] | undefined,
): boolean {
  if (userId && po.pr_requester_id === userId) return true
  return !!roles?.includes('erp_pa_officer') && !po.pr_id && po.source === 'nc'
}
