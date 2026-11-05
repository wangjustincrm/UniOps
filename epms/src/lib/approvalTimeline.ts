import type { ApiEvent } from '@/services/pr'
import type { ApprovalStep, CurrentStep } from '@/types'
import { isSkipped } from '@/lib/approvalHistory'

/** Statuses in which the document sits on an approver's open task. */
const AWAITING = ['submitted', 'in_review']

/** The events of the approval run the document is in now. A recall turns it
 *  back into a draft and its next submission starts over at the top, so the
 *  approvals before the last recall no longer stand — showing them named the
 *  previous round's approver on a step now waiting on someone else (ISS-0085). */
export function currentRunEvents(events: ApiEvent[]): ApiEvent[] {
  let start = 0
  events.forEach((e, i) => {
    if (e.action === 'recall') start = i + 1
  })
  return events.slice(start)
}

export interface NodeState {
  status: ApprovalStep['status']
  /** The event shown on the step: its approval (or skip), the return, or the reject. */
  event?: ApiEvent
}

/** Where each workflow node stands, read from the document's status, its step
 *  index and its events — shared by the PR, PO, Agreement and PA timelines so
 *  they all answer the same way (ISS-0084, ISS-0085, ISS-0088).
 *
 *  `return` resets approval_step_idx to 0 and the resubmission resumes at the
 *  step that returned it (approval-api resume_step), so for a returned document
 *  the return event — which records its step — says how far it got. A reject
 *  leaves the index on the rejecting step; PA records it as `cancelled`. A
 *  cancel is not anyone's decision at a step, so nothing is marked for it.
 *  Only an engine skip shows as skipped. */
export function approvalNodeStates(
  count: number,
  status: string,
  stepIdx: number,
  events: ApiEvent[],
  doneStatuses: readonly string[],
): NodeState[] {
  const run = currentRunEvents(events)
  // Latest approval per step: a step re-approved after a return shows the
  // approval that stands now.
  const approvedAt: Record<number, ApiEvent> = {}
  for (const e of run) if (e.action === 'approve') approvedAt[e.step_idx] = e
  const lastOf = (actions: string[]) => [...run].reverse().find((e) => actions.includes(e.action))

  let stop: { idx: number; status: 'returned' | 'rejected'; event: ApiEvent } | undefined
  if (status === 'returned') {
    const e = lastOf(['return'])
    if (e) stop = { idx: e.step_idx, status: 'returned', event: e }
  } else if (status === 'rejected' || status === 'cancelled') {
    const e = lastOf(['reject', 'cancel'])
    if (e?.action === 'reject') stop = { idx: e.step_idx, status: 'rejected', event: e }
  }
  const reached = stop ? stop.idx : stepIdx

  return Array.from({ length: count }, (_, i): NodeState => {
    const approved = approvedAt[i]
    const passed: NodeState = { status: isSkipped(approved) ? 'skipped' : 'completed', event: approved }
    if (status === 'draft') return { status: 'pending' }
    if (doneStatuses.includes(status)) return passed
    if (i < reached) return passed
    if (i === reached) {
      if (stop) return { status: stop.status, event: stop.event }
      if (AWAITING.includes(status)) return { status: 'current' }
    }
    return { status: 'pending' }
  })
}

const daysSince = (iso: string) =>
  Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000))

/** The workflow nodes as ApprovalTimeline renders them.
 *
 *  - A skipped step carries its reason (PRD §3.2.4, ISS-0084), never a name.
 *  - The current step names who it is waiting on, stand-in included, from the
 *    open task (`current_step` on the detail response), not from an earlier
 *    approval of the same index (ISS-0085).
 *  - A returned / rejected document marks the step that decided it, with who,
 *    when and their comment (REJ-003, ISS-0088). */
export function buildApprovalNodes(opts: {
  nodes: { id: string; label: string }[]
  status: string
  stepIdx: number
  events?: ApiEvent[]
  currentStep?: CurrentStep | null
  doneStatuses: readonly string[]
  /** Who a returned document now waits on, e.g. "requester". */
  ownerLabel: string
}): ApprovalStep[] {
  const states = approvalNodeStates(
    opts.nodes.length, opts.status, opts.stepIdx, opts.events ?? [], opts.doneStatuses)
  return opts.nodes.map((node, i) => {
    const { status, event } = states[i]
    const step: ApprovalStep = { id: node.id, role: node.label, status }
    if (status === 'skipped') {
      step.note = event?.skip_reason || undefined
    } else if (status === 'completed') {
      step.actorName = event?.actor_name ?? undefined
      step.completedAt = event?.created_at
    } else if (status === 'current') {
      step.actorName = opts.currentStep?.approver_name ?? undefined
      if (opts.currentStep?.since) step.daysWaiting = daysSince(opts.currentStep.since)
    } else if (status === 'returned' || status === 'rejected') {
      step.actorName = event?.actor_name ?? undefined
      step.completedAt = event?.created_at
      step.action = status === 'returned' ? 'Returned' : 'Rejected'
      step.comment = event?.comment ?? undefined
      if (status === 'returned') {
        step.note = `Returned for revision — waiting for the ${opts.ownerLabel} to resubmit`
      }
    }
    return step
  })
}
