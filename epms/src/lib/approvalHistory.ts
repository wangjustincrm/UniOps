import type { ApiEvent } from '@/services/pr'

/** One History-tab row, as the four detail pages (PR, PO, PA, Agreement) print it. */
export interface HistoryLine {
  /** "approve", "Skipped", "reject"… — rendered capitalised. */
  action: string
  /** "by finance manager", or "director step" for a skipped step. */
  role: string
  /** Whose action it was; null when no person acted on that step. */
  actorName: string | null
  /** The person's comment, or why the step was skipped. */
  note: string | null
}

const words = (s: string) => s.replace(/_/g, ' ')

// A step token that more than one actor role fulfils (same rule as epms-api
// crud/signatories.py::_step_matches).
const STEP_ROLE_ALIASES: Record<string, string[]> = { gm_or_opm: ['gm', 'opm'] }

/** A workflow step as the engine / config lists it; `role` may be absent
 *  (an agreement's configured nodes carry none), and then no label is used. */
type StepDef = { label: string; role?: string }

/** The step's name as configured in the approval engine (e.g. "AP Review") —
 *  ISS-0107/0144, rule question 6: the approval flow is whatever the engine is
 *  configured with. Used only while the event's step still has the role the
 *  actor acted in (the workflow may have been edited since); otherwise null and
 *  the role name is shown as before. */
export function configuredStepLabel(e: ApiEvent, steps?: readonly StepDef[] | null): string | null {
  const step = steps?.[e.step_idx]
  if (!step?.label || !step.role || !e.actor_role) return null
  const matches = e.actor_role === step.role || (STEP_ROLE_ALIASES[step.role] ?? []).includes(e.actor_role)
  return matches ? step.label : null
}

/** Reads the event's `kind` (derived once by epms-api), never its comment text.
 *
 *  A skipped step is stored as an "approve" whose actor is whoever triggered
 *  the skip — the requester, or the approver of the step before. Printed as
 *  is, History read "Approve by finance manager — <the requester>" (ISS-0077).
 *  A skip names the step and the reason, never a person; a PMS-import backfill
 *  drops the import account's name for the same reason the PDF does. */
export function historyLine(e: ApiEvent, steps?: readonly StepDef[] | null): HistoryLine {
  const configured = configuredStepLabel(e, steps)
  const role = configured ?? words(e.actor_role)
  if (e.kind === 'skipped') {
    return { action: 'Skipped', role: `${role} step`, actorName: null, note: e.skip_reason ?? null }
  }
  if (e.kind === 'imported') {
    return { action: words(e.action), role: `by ${role} (recorded at PMS import)`, actorName: null, note: e.comment ?? null }
  }
  return { action: words(e.action), role: `by ${role}`, actorName: e.actor_name ?? null, note: e.comment ?? null }
}

/** For the approval timelines: was this step skipped rather than approved? */
export const isSkipped = (e: ApiEvent | undefined): boolean => e?.kind === 'skipped'
