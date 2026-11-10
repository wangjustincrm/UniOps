/**
 * Tables in Assistant replies (ISS-0111): open by default, fold to one line,
 * sort by any column with an arrow showing the order, filter, and say so when
 * the sort cannot cover the whole result.
 */
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Markdown } from './markdown'
import { Assistant, resultRows } from './Assistant'
import { sortKey, splitTotal } from './table'
import type { AssistantReply } from './types'

const MD = [
  '| PA | Vendor | Amount | Date |',
  '|----|--------|--------|------|',
  '| PA-1 | Beta Ltd | 1,200.00 CAD | 16/10/2026 |',
  '| PA-2 | alpha inc | 99.50 CAD | 02/10/2026 |',
  '| PA-3 | Gamma | 15,000.00 CAD | 05/09/2026 |',
  '| PA-4 | Delta | — | 01/11/2026 |',
  '| **Total** | | **16,299.50 CAD** | |',
].join('\n')

const col = (container: HTMLElement, i: number) =>
  [...container.querySelectorAll('tbody tr')].map((tr) => tr.querySelectorAll('td')[i]?.textContent)

describe('answer tables (ISS-0111)', () => {
  it('is open by default and folds to one line and back', () => {
    const { container } = render(<Markdown text={MD} />)
    expect(container.querySelector('table')).not.toBeNull()
    fireEvent.click(screen.getByText('4 rows — hide table'))
    expect(container.querySelector('table')).toBeNull()
    fireEvent.click(screen.getByText('4 rows — show table'))
    expect(container.querySelector('table')).not.toBeNull()
  })

  it('sorts by a column header, ascending then descending, with the arrow', () => {
    const { container } = render(<Markdown text={MD} resultRows={4} />)
    const amount = screen.getByRole('button', { name: /Amount/ })
    fireEvent.click(amount)
    expect(screen.getByRole('columnheader', { name: /Amount/ })).toHaveAttribute('aria-sort', 'ascending')
    // By value, not as text ("15,000" < "99.50" as text); the blank last; the
    // Total row stays at the bottom.
    expect(col(container, 0)).toEqual(['PA-2', 'PA-1', 'PA-3', 'PA-4', 'Total'])
    fireEvent.click(amount)
    expect(screen.getByRole('columnheader', { name: /Amount/ })).toHaveAttribute('aria-sort', 'descending')
    expect(col(container, 0)).toEqual(['PA-3', 'PA-1', 'PA-2', 'PA-4', 'Total'])
    // Day-first dates sort by date.
    fireEvent.click(screen.getByRole('button', { name: /Date/ }))
    expect(col(container, 0)).toEqual(['PA-3', 'PA-2', 'PA-1', 'PA-4', 'Total'])
    // Text ignores case.
    fireEvent.click(screen.getByRole('button', { name: /Vendor/ }))
    expect(col(container, 1)).toEqual(['alpha inc', 'Beta Ltd', 'Delta', 'Gamma', ''])
    // The table holds the whole result: no "only the rows shown" note.
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('filters rows by any cell', () => {
    const { container } = render(<Markdown text={MD} resultRows={4} />)
    fireEvent.change(screen.getByLabelText('Filter rows'), { target: { value: 'GAM' } })
    expect(col(container, 0)).toEqual(['PA-3', 'Total'])
    fireEvent.change(screen.getByLabelText('Filter rows'), { target: { value: 'nobody' } })
    expect(screen.getByText('No rows match the filter.')).toBeTruthy()
  })

  it('says the sort covers only the rows shown when the result has more', () => {
    render(<Markdown text={MD} resultRows={23} />)
    expect(screen.queryByRole('note')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Amount/ }))
    expect(screen.getByRole('note').textContent).toMatch(
      /only to the 4 rows shown here, not the whole result \(23 rows\) — Export to Excel has every row/)
  })

  it('says so too when it cannot tell whether the table is the whole result', () => {
    render(<Markdown text={MD} />)
    fireEvent.click(screen.getByRole('button', { name: /PA/ }))
    expect(screen.getByRole('note').textContent).toMatch(/not necessarily the whole result/)
  })

  it('reads numbers, money, blanks and dates as such', () => {
    expect(sortKey('**1,234.56 CAD**')).toBe(1234.56)
    expect(sortKey('$ 12')).toBe(12)
    expect(sortKey('(500.00)')).toBe(-500)
    expect(sortKey('-3')).toBe(-3)
    expect(sortKey('—')).toBeNull()
    expect(sortKey('16/10/2026')).toBe('2026-10-16')
    expect(sortKey('PO-079-2605-03')).toBe('PO-079-2605-03')
    expect(splitTotal([['x'], ['合计']]).total).toEqual(['合计'])
    expect(splitTotal([['合计']]).total).toBeNull()
  })

  it('measures against the whole result only for a query answer', () => {
    const q = { entity: 'payment_application' }
    expect(resultRows({ answer: '', kind: 'answer', query: q,
      sources: { row_count: 10, truncated: false } } as AssistantReply)).toBe(10)
    expect(resultRows({ answer: '', kind: 'answer', query: q,
      sources: { row_count: 100, truncated: true, total_rows: 486 } } as AssistantReply)).toBe(486)
    expect(resultRows({ answer: '', kind: 'answer', query: q,
      sources: { row_count: 100, truncated: true, total_rows: null } } as AssistantReply)).toBeNull()
    expect(resultRows({ answer: '', kind: 'parts', query: null, sources: null } as AssistantReply)).toBeNull()
  })
})

describe('in the panel', () => {
  beforeAll(() => {
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  })

  it('a reply listing 4 of 23 rows says the sort is of the rows shown; export is unchanged', async () => {
    const reply: AssistantReply = {
      answer: '10 月 16 日处理了 23 个付款申请，以下是前 4 个：\n\n' + MD,
      kind: 'answer',
      query: { entity: 'payment_application' },
      sources: { entity: 'payment_application', row_count: 23, truncated: false },
    }
    const exportQuery = vi.fn().mockResolvedValue(undefined)
    render(<Assistant ask={vi.fn().mockResolvedValue(reply)} exportQuery={exportQuery} />)
    fireEvent.click(screen.getByLabelText('Open assistant'))
    fireEvent.change(await screen.findByPlaceholderText('Ask a question…'),
      { target: { value: '10 月 16 日处理了哪些付款申请？' } })
    await act(async () => { fireEvent.click(screen.getByLabelText('Send')) })

    fireEvent.click(await screen.findByRole('button', { name: /Amount/ }))
    expect(screen.getByRole('note').textContent).toMatch(/not the whole result \(23 rows\)/)
    await act(async () => { fireEvent.click(screen.getByText('Export to Excel')) })
    expect(exportQuery).toHaveBeenCalledWith({ entity: 'payment_application' }, '10 月 16 日处理了哪些付款申请？')
  })
})
