/**
 * Wires the shared Assistant panel to the Portal.
 *
 * The Portal is where everyone lands, so for most people this is the first — and
 * for the ones who were never trained on the system, possibly the only — place
 * they will find it. The panel itself lives in @uniops/shell and knows nothing
 * about this app; this file is the whole of the Portal half.
 *
 * It answers out of EPMS. The Portal has no API of its own to ask, and the
 * questions people bring here are about purchasing regardless of which tile they
 * came from, so every call goes to epms-api under the caller's own token — which
 * means the same scope rules apply as if they had asked from inside EPMS.
 *
 * No document context is derived. The Portal's routes are tiles and admin pages,
 * not documents; "this PO" has no referent here, and inventing one from a URL
 * that does not contain a document id would be worse than leaving it out.
 */
import { Assistant, type AssistantContext, type AssistantReply } from '@uniops/shell'
import { epmsApi, epmsPostDownload } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { useLocation } from 'react-router-dom'

async function ask(
  message: string,
  context: AssistantContext,
  history: { role: 'user' | 'assistant'; text: string }[]
): Promise<AssistantReply> {
  return epmsApi.post<AssistantReply>('/assistant/chat', { message, context, history })
}

async function describeScope(): Promise<string[]> {
  const res = await epmsApi.get<{ entities: { label: string }[] }>('/assistant/schema')
  // Labels read "Purchase Order (PO) — the order placed with a vendor"; the part
  // before the dash is the name, which is all a one-line summary should carry.
  return res.entities.map((e) => e.label.split('\u2014')[0].trim())
}

async function exportQuery(
  query: Record<string, unknown>,
  question: string
): Promise<void> {
  await epmsPostDownload('/assistant/export', { ...query, question }, 'report.xlsx')
}

export function AssistantMount() {
  const { pathname } = useLocation()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  // Nothing to ask about on the login screen, and every call would 401.
  if (!isAuthenticated) return null

  return (
    <Assistant
      ask={ask}
      describeScope={describeScope}
      exportQuery={exportQuery}
      context={{ app: 'portal', route: pathname }}
    />
  )
}
