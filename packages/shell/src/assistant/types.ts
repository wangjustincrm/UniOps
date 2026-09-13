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
}

export type AssistantKind =
  | 'answer'
  | 'preflight'
  | 'cannot_answer'
  | 'denied'

export interface AssistantReply {
  answer: string
  kind: AssistantKind
  reason?: string | null
  /** The query that ran, when one did. Shown as the receipt. */
  query?: Record<string, unknown> | null
  preflight?: AssistantPreflight | null
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
