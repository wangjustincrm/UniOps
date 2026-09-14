/**
 * Wires the shared Assistant panel to MRP.
 *
 * The ontology now covers the planning tables, so questions asked here are
 * answered about the plan this app is showing — with one guarantee worth
 * knowing: the planning entities are scoped to the plan in force, the current
 * forecast version and the newest suggestion run. Superseded runs are not
 * reachable, which is deliberate. Two released MPS runs hold 96 and 92 lines
 * for the same materials; an answer that quietly summed both would be a near
 * doubling with nothing odd-looking about it.
 *
 * Everything goes to epms-api under the caller's own token. The MRP entities
 * gate on mrp.report.view, so someone without it sees the purchasing entities
 * and nothing else, exactly as they would anywhere.
 */
import { useLocation } from 'react-router-dom'
import { Assistant, type AssistantContext, type AssistantReply } from '@uniops/shell'
import { epmsApi, epmsPostDownload } from '@/lib/api'
import { useMrpAuth } from '@/store/auth'

async function ask(
  message: string,
  context: AssistantContext,
  history: { role: 'user' | 'assistant'; text: string }[]
): Promise<AssistantReply> {
  return epmsApi.post<AssistantReply>('/api/v1/assistant/chat', {
    message, context, history,
  })
}

async function describeScope(): Promise<string[]> {
  const res = await epmsApi.get<{ entities: { label: string }[] }>(
    '/api/v1/assistant/schema')
  return res.entities.map((e) => e.label.split('—')[0].trim())
}

async function exportQuery(
  query: Record<string, unknown>,
  question: string
): Promise<void> {
  await epmsPostDownload('/api/v1/assistant/export', { ...query, question },
                         'report.xlsx')
}

export function AssistantMount() {
  const { pathname } = useLocation()
  const isAuthenticated = useMrpAuth((s) => s.isAuthenticated)

  if (!isAuthenticated) return null

  return (
    <Assistant
      ask={ask}
      describeScope={describeScope}
      exportQuery={exportQuery}
      context={{ app: 'mrp', route: pathname }}
    />
  )
}
