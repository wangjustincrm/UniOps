/**
 * Is the viewer the approver currently holding a document (PRD §3.2.4: the
 * requester actions are hidden from "the current approver")?
 *
 * GET /tasks is already scoped to the viewer for everyone but a System Admin,
 * who gets every open task in the system (epms-api crud/task.py::get_for_role).
 * So for an admin an open approve task only counts when it is theirs: assigned
 * to them by name, or an unassigned pool task for a role they really hold
 * (primary or additional). Director / Supervisor tasks are never a pool. A
 * System Admin is otherwise never a step holder (§3.3.5); counting everyone's
 * tasks hid Recall to Edit from admins on every submitted PR (ISS-0156).
 */
export interface OpenTask {
  document_id: string
  type: string
  assigned_role: string
  assigned_user_id?: string | null
}

const PERSONAL_APPROVAL_ROLES = new Set(['director', 'supervisor'])

export function holdsApproveTask(
  tasks: OpenTask[],
  documentId: string,
  taskType: string,
  viewer: { id?: string | null; role?: string | null },
  viewerRoles: string[] = [],
): boolean {
  const mine = tasks.filter((t) => t.document_id === documentId && t.type === taskType)
  if (viewer.role !== 'system_admin') return mine.length > 0
  const roles = new Set([...viewerRoles, viewer.role])
  return mine.some((t) =>
    t.assigned_user_id
      ? t.assigned_user_id === viewer.id
      : roles.has(t.assigned_role) && !PERSONAL_APPROVAL_ROLES.has(t.assigned_role),
  )
}
