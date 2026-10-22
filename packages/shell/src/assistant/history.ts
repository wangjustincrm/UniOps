import type { AssistantMessage, AssistantTurn } from './types'

/**
 * The conversation sent back with a new question: answered exchanges only.
 *
 * A question whose request failed ("Failed to fetch") has no answer, and
 * sending it anyway put two or three user turns in a row. The model reads
 * consecutive user turns as one request, so after two failures a question
 * about a PO was planned as the earlier goods-receipt question and answered
 * "that PO does not exist" (ISS-0071). Dropping only the error message, as
 * this used to, left the unanswered question behind; the pair goes together.
 *
 * An answer's query rides along with its text: a follow-up like "drop the
 * second rule" has to be rebuilt from what actually ran, and the prose does
 * not say which filters that was.
 */
export function buildHistory(messages: AssistantMessage[]): AssistantTurn[] {
  const turns: AssistantTurn[] = []
  for (let i = 0; i < messages.length; i++) {
    const question = messages[i]
    const answer = messages[i + 1]
    if (question.role !== 'user') continue
    if (!answer || answer.role !== 'assistant' || answer.error) continue
    turns.push(
      { role: 'user', text: question.text, query: null },
      { role: 'assistant', text: answer.text, query: answer.reply?.query ?? null },
    )
  }
  return turns
}
