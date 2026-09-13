/**
 * Shapes returned by POST /assistant/chat.
 *
 * The panel is deliberately ignorant of how the answer was produced — it takes
 * an `ask` function from the host app, so this package never learns any app's
 * API client, auth store, or base URL.
 */

/** One gate from a preflight result. */
export interface AssistantCheck {
  id: string
  /** field | rule | config — which service owns the gate. */
  layer: string
  passed: boolean
  message: string | null
  /** The distinction the whole panel is built around: yours to fix, or not. */
  fixable_by_user: boolean
  owner: string | null
  fix_route: string | null
}

export interface AssistantPreflight {
  doc_type: string
  doc_id: string
  document_number: string | null
  action: string
  allowed: boolean
  /** false when the approval engine could not be reached — the list is partial. */
  complete: boolean
  checks: AssistantCheck[]
}

export interface AssistantSources {
  entity?: string
  row_count?: number
  truncated?: boolean
  document?: string
  allowed?: boolean
  complete?: boolean
  status?: string | null
  steps?: number
  events?: number
}

export type AssistantKind =
  | 'answer'
  | 'preflight'
  | 'workflow'
  | 'cannot_answer'
  | 'denied'

/** One step of an approval chain, as the engine defines it. */
export interface AssistantWorkflowStep {
  id?: string
  role?: string
  label?: string
}

/** One thing that has already happened to a document. */
export interface AssistantWorkflowEvent {
  step_idx: number | null
  action: string | null
  actor_name: string | null
  actor_role: string | null
  comment: string | null
  at: string | null
}

export interface AssistantWorkflow {
  doc_type: string
  document_number: string
  status: string | null
  steps: AssistantWorkflowStep[]
  /** false when the approval engine could not be reached — chain is unknown. */
  steps_available: boolean
  current_step: {
    role: string
    label: string
    /** null for a broadcast step: it is with whoever holds the role. */
    approver_name: string | null
    since: string | null
  } | null
  history: AssistantWorkflowEvent[]
}

export interface AssistantReply {
  answer: string
  kind: AssistantKind
  reason?: string | null
  /** The query that ran, when one did. Shown as the receipt. */
  query?: Record<string, unknown> | null
  preflight?: AssistantPreflight | null
  workflow?: AssistantWorkflow | null
  sources?: AssistantSources | null
}

/** Where the person is, so "this PO" has a referent. */
export interface AssistantContext {
  app?: string
  route?: string
  doc_type?: string
  doc_id?: string
}

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  reply?: AssistantReply
  /** Set when the request itself failed, as opposed to the model declining. */
  error?: boolean
}
