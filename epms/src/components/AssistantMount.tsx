/**
 * Wires the shared Assistant panel to EPMS.
 *
 * The panel itself lives in @uniops/shell and knows nothing about this app — it
 * takes an `ask` function, which is what keeps the shared package free of any
 * API client or auth store. This file is the whole of the EPMS half.
 *
 * Page context is what makes "why can't I submit this?" resolvable without the
 * person typing a document number. It is derived from the URL rather than from
 * the tab store on purpose: the tab store is absent in the iframe-embedded mode,
 * and a global overlay must never be the reason a page fails to render.
 */
import { useLocation } from 'react-router-dom'
import { Assistant, type AssistantContext, type AssistantReply } from '@uniops/shell'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

/** URL → what document the person is looking at, if any. */
export function deriveContext(pathname: string): AssistantContext {
  const ctx: AssistantContext = { app: 'epms', route: pathname }
  // /pr/<id>, /po/<id>, /gr/<id>… — the id is only useful when it is an id, not
  // a sub-route like /pr/new or /pr/list.
  const m = pathname.match(
    /^\/(pr|po|gr|invoice|invoices|pa)\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
  )
  if (m) {
    ctx.doc_type = m[1].toLowerCase().replace(/^invoices$/, 'invoice')
    ctx.doc_id = m[2]
  }
  return ctx
}

async function ask(
  message: string,
  context: AssistantContext,
  history: { role: 'user' | 'assistant'; text: string }[]
): Promise<AssistantReply> {
  return api.post<AssistantReply>('/assistant/chat', { message, context, history })
}

/** What this person can actually be answered about, straight from the ontology. */
async function describeScope(): Promise<string[]> {
  const res = await api.get<{ entities: { label: string }[] }>('/assistant/schema')
  // Labels read "Purchase Order (PO) — the order placed with a vendor"; the part
  // before the dash is the name, which is all that belongs in a one-line summary.
  return res.entities.map((e) => e.label.split('—')[0].trim())
}

export function AssistantMount() {
  const { pathname } = useLocation()
  // Same field AppLayout gates on, rather than a second definition of
  // "signed in" that could drift from it.
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  // No panel on the login screen: there is nothing to ask about, and every call
  // it could make would 401.
  if (!isAuthenticated) return null

  return <Assistant ask={ask} describeScope={describeScope} context={deriveContext(pathname)} />
}
