/**
 * A message that asks several things (ISS-0037, ISS-0069) comes back as one
 * answer with a part per question; each part keeps its own receipt, so the
 * query behind the invoice amount can be checked and exported on its own.
 */
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Assistant } from './Assistant'
import type { AssistantReply } from './types'

const reply: AssistantReply = {
  answer: '**1. PO-079-2605-03 是什么状态？**\n\nIssued.\n\n**2. INV-2026-0123 金额多少？**\n\n199.34 CAD',
  kind: 'parts',
  query: null,
  sources: null,
  parts: [
    {
      question: 'PO-079-2605-03 是什么状态？', answer: 'Issued.', kind: 'answer',
      query: { entity: 'purchase_order' }, sources: { entity: 'purchase_order', row_count: 1 },
    },
    {
      question: 'INV-2026-0123 金额多少？', answer: '199.34 CAD', kind: 'answer',
      query: { entity: 'invoice' }, sources: { entity: 'invoice', row_count: 1 },
    },
    { question: '帮我写一首诗', answer: '这不在我能做的范围内。', kind: 'cannot_answer', query: null, sources: null },
  ],
}

describe('several questions in one message', () => {
  beforeAll(() => {
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  })

  it('shows each part with its own evidence and export', async () => {
    const exportQuery = vi.fn().mockResolvedValue(undefined)
    render(<Assistant ask={vi.fn().mockResolvedValue(reply)} exportQuery={exportQuery} />)
    fireEvent.click(screen.getByLabelText('Open assistant'))
    fireEvent.change(await screen.findByPlaceholderText('Ask a question…'),
      { target: { value: 'PO-079-2605-03 是什么状态，INV-2026-0123 金额多少，再帮我写一首诗' } })
    await act(async () => { fireEvent.click(screen.getByLabelText('Send')) })

    expect(await screen.findByText(/Show evidence · purchase_order — 1 row/)).toBeTruthy()
    expect(screen.getByText(/Show evidence · invoice — 1 row/)).toBeTruthy()
    // A part with nothing behind it (the refused poem) adds no empty block.
    expect(screen.queryByText('3. 帮我写一首诗')).toBeNull()

    const exports = screen.getAllByText('Export to Excel')
    expect(exports).toHaveLength(2)
    await act(async () => { fireEvent.click(exports[1]) })
    expect(exportQuery).toHaveBeenCalledWith({ entity: 'invoice' }, 'INV-2026-0123 金额多少？')
  })
})
