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

/** Reads the event's `kind` (derived once by epms-api), never its comment text.
 *
 *  A skipped step is stored as an "approve" whose actor is whoever triggered
 *  the skip — the requester, or the approver of the step before. Printed as
 *  is, History read "Approve by finance manager — <the requester>" (ISS-0077).
 *  A skip names the step and the reason, never a person; a PMS-import backfill
 *  drops the import account's name for the same reason the PDF does. */
export function historyLine(e: ApiEvent): HistoryLine {
  const role = words(e.actor_role)
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
