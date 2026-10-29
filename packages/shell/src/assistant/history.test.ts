import { describe, expect, it } from 'vitest'
import { buildHistory } from './history'
import type { AssistantMessage } from './types'

const user = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })
const answer = (id: string, text: string, query?: Record<string, unknown>): AssistantMessage => ({
  id,
  role: 'assistant',
  text,
  reply: query ? ({ query } as unknown as AssistantMessage['reply']) : undefined,
})
const failed = (id: string): AssistantMessage => ({
  id,
  role: 'assistant',
  text: 'Failed to fetch',
  error: true,
})

describe('Assistant history (ISS-0071)', () => {
  it('drops a failed question together with its error', () => {
    const turns = buildHistory([
      user('1', '今天收了哪些货？'),
      failed('2'),
      user('3', '今天收了哪些货？'),
      failed('4'),
    ])
    expect(turns).toEqual([])
  })

  it('never sends two user turns in a row', () => {
    const turns = buildHistory([
      user('1', 'PO-079 的状态？'),
      answer('2', 'Issued', { entity: 'purchase_order' }),
      user('3', '今天收了哪些货？'),
      failed('4'),
      user('5', '它的供应商是谁？'),
      answer('6', 'Acme'),
    ])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(turns.map((t) => t.text)).toEqual(['PO-079 的状态？', 'Issued', '它的供应商是谁？', 'Acme'])
  })

  it("keeps an answer's query for follow-ups", () => {
    const turns = buildHistory([user('1', 'q'), answer('2', 'a', { entity: 'invoice' })])
    expect(turns[1].query).toEqual({ entity: 'invoice' })
    expect(turns[0].query).toBeNull()
  })
})
