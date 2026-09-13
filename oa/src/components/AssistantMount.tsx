/**
 * Wires the shared Assistant panel to OA.
 *
 * Same arrangement as EPMS and the Portal: the panel lives in @uniops/shell and
 * takes an `ask` function, so the shared package never learns this app's API
 * client or auth store.
 *
 * OA's own work — reimbursements, mileage, vendor payments — sits on the far
 * side of the same purchasing chain the assistant knows, and the questions that
 * arrive here are usually about that chain: where a PA went, whether an invoice
 * matched, why a payment has not been raised. Those are answered out of EPMS
 * under the caller's own token, so the scope rules are the ones EPMS applies.
 *
 * Context is the route only. OA's document ids are its own, and the assistant's
 * preflight resolves EPMS documents; handing it an OA id would make "this one"
 * mean the wrong record rather than nothing, which is the worse failure.
 */
import { useLocation } from 'react-router-dom'
import { Assistant, type AssistantContext, type AssistantReply } from '@uniops/shell'
import { epmsApi, epmsPostDownload } from '@/lib/api'
import { useOaAuth } from '@/store/auth'

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
  // Labels read "Purchase Order (PO) — the order placed with a vendor"; the part
  // before the dash is the name, which is all a one-line summary should carry.
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
  const isAuthenticated = useOaAuth((s) => s.isAuthenticated)

  // Nothing to ask about before sign-in, and every call would 401.
  if (!isAuthenticated) return null

  return (
    <Assistant
      ask={ask}
      describeScope={describeScope}
      exportQuery={exportQuery}
      context={{ app: 'oa', route: pathname }}
    />
  )
}
