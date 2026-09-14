/**
 * Wires the shared Assistant panel to Finance.
 *
 * This is the first front-end where the assistant can answer about the module
 * it is mounted in: the ontology now covers the ledger, payables, the chart of
 * accounts and the bank accounts, so a question asked here is answered from the
 * same books this app is showing.
 *
 * Access is unchanged by where the question is asked. Every call goes to
 * epms-api under the caller's own token, and the finance entities gate on
 * view_finance — someone who opens this page without that permission gets the
 * purchasing entities and nothing else, exactly as they would in EPMS.
 *
 * Context is the route only: Finance's ids are its own, and the assistant's
 * preflight resolves EPMS documents.
 */
import { useLocation } from 'react-router-dom'
import { Assistant, type AssistantContext, type AssistantReply } from '@uniops/shell'
import { epmsApi, epmsPostDownload } from '@/lib/api'
import { useAuthStore } from '@/store/auth'

async function ask(
  message: string,
  context: AssistantContext,
  history: { role: 'user' | 'assistant'; text: string }[]
): Promise<AssistantReply> {
  return epmsApi.post<AssistantReply>('/assistant/chat', { message, context, history })
}

async function describeScope(): Promise<string[]> {
  const res = await epmsApi.get<{ entities: { label: string }[] }>('/assistant/schema')
  // Labels read "Journal Voucher (JV) — one accounting entry"; the part before
  // the dash is the name, which is all a one-line summary should carry.
  return res.entities.map((e) => e.label.split('—')[0].trim())
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

  if (!isAuthenticated) return null

  return (
    <Assistant
      ask={ask}
      describeScope={describeScope}
      exportQuery={exportQuery}
      context={{ app: 'finance', route: pathname }}
    />
  )
}
