/**
 * Marked questions (ISS-0062). The endpoints were fine; what the panel did with
 * a failure was not: a list that failed to load disabled every star in silence,
 * and a refused mark ("10 at most") was reported only inside the Marked list,
 * so clicking a star looked like nothing happened.
 */
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Assistant } from './Assistant'
import type { AssistantReply, AssistantSavedApi, AssistantSavedList } from './types'

const reply: AssistantReply = { answer: 'Issued.', kind: 'answer', query: null, sources: null }

async function openAndAsk(question: string) {
  fireEvent.click(screen.getByLabelText('Open assistant'))
  const box = await screen.findByPlaceholderText('Ask a question…')
  fireEvent.change(box, { target: { value: question } })
  await act(async () => { fireEvent.click(screen.getByLabelText('Send')) })
  await screen.findByText('Issued.')
}

describe('Marked questions', () => {
  beforeAll(() => {
    // jsdom has no layout, so no scrollTo; the panel scrolls to each new message.
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  })

  it('says the list failed to load, keeps the stars usable, and can retry', async () => {
    const full: AssistantSavedList = { items: [{ id: 'q1', question: 'PO-576 status?' }], limit: 10 }
    const saved: AssistantSavedApi = {
      list: vi.fn().mockRejectedValueOnce(new Error('Failed to fetch')).mockResolvedValue(full),
      add: vi.fn().mockResolvedValue(full),
      remove: vi.fn(),
    }
    render(<Assistant ask={vi.fn().mockResolvedValue(reply)} saved={saved} />)
    fireEvent.click(screen.getByLabelText('Open assistant'))
    expect(await screen.findByText(/Could not load your marked questions: Failed to fetch/)).toBeTruthy()

    const box = screen.getByPlaceholderText('Ask a question…')
    fireEvent.change(box, { target: { value: 'PO-576 status?' } })
    await act(async () => { fireEvent.click(screen.getByLabelText('Send')) })
    await screen.findByText('Issued.')

    const star = screen.getByLabelText('Mark this question to ask again later') as HTMLButtonElement
    expect(star.disabled).toBe(false)
    await act(async () => { fireEvent.click(star) })
    expect(saved.add).toHaveBeenCalledWith('PO-576 status?')
    // Marking returns the whole list, so the header count is back.
    expect(await screen.findByText('Marked (1)')).toBeTruthy()
  })

  it('reports a refused mark next to the star that was clicked', async () => {
    const tenOthers: AssistantSavedList = {
      items: Array.from({ length: 10 }, (_, i) => ({ id: `q${i}`, question: `other ${i}` })),
      limit: 10,
    }
    const saved: AssistantSavedApi = {
      list: vi.fn().mockResolvedValue(tenOthers),
      add: vi.fn().mockRejectedValue(
        new Error('You can keep up to 10 marked questions. Unmark one first.')),
      remove: vi.fn(),
    }
    render(<Assistant ask={vi.fn().mockResolvedValue(reply)} saved={saved} />)
    await openAndAsk('INV-2026-0123 amount?')

    const star = await screen.findByLabelText(/You can keep 10 marked questions/)
    await act(async () => { fireEvent.click(star) })
    // In the conversation, without opening the Marked list.
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('You can keep up to 10 marked questions')
  })

  it('retries loading the list on request', async () => {
    const empty: AssistantSavedList = { items: [], limit: 10 }
    const saved: AssistantSavedApi = {
      list: vi.fn().mockRejectedValueOnce(new Error('Failed to fetch')).mockResolvedValue(empty),
      add: vi.fn(),
      remove: vi.fn(),
    }
    render(<Assistant ask={vi.fn()} saved={saved} />)
    fireEvent.click(screen.getByLabelText('Open assistant'))
    await screen.findByText(/Could not load your marked questions/)
    await act(async () => { fireEvent.click(screen.getByText('Try again')) })
    await waitFor(() => expect(saved.list).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.queryByText(/Could not load your marked questions/)).toBeNull())
  })
})
