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
