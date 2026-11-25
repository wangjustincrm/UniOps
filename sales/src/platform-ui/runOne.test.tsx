import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ActionButton, ActionRefused, ConfirmAction, NoticeScope, actionDone, businessErrorOf, runOne, useActionSends, type ActionResultRow, type ListMeta } from './index'

// One record through its record action (stage 3 l): what was left out or failed is an error, never a success.
const client = (row: Partial<ActionResultRow> | null) => ({ action: vi.fn(async () => ({ action: 'x', dry_run: false, counts: { done: 0, skipped: 0, failed: 0 },
  results: row ? [{ id: 7, status: 'done', ...row } as ActionResultRow] : [] })) })
const caught = async (p: Promise<unknown>) => p.then(() => null, (e: unknown) => e)

describe('runOne', () => {
  it('sends the record by the version seen, with what is given, and gives the result back', async () => {
    const c = client({ note: 'queued' })
    expect(await runOne(c, 'send', { id: 7, version: 3 }, { reason: 'lost' })).toMatchObject({ status: 'done', note: 'queued' })
    expect(c.action).toHaveBeenCalledWith('send', { ids: { 7: 3 }, reason: 'lost' })
    await runOne(c, 'ignore', { id: 7, version: null }, { input: { a: 1 } })
    expect(c.action).toHaveBeenLastCalledWith('ignore', { ids: { 7: null }, input: { a: 1 } })
  })

  it.each([
    [{ status: 'skipped', reason: 'already queued', code: 'send.queued' }, 'send.queued', 'already queued'],
    [{ status: 'skipped', reason: 'not in review' }, 'action.skipped', 'not in review'],
    [{ status: 'failed', reason: 'changed since you selected it: reload and look again', code: 'entity.stale' }, 'entity.stale', 'changed since you selected it: reload and look again'],
    [{ status: 'failed' }, 'action.failed', 'It was not done.'],
    [null, 'action.unknown', 'It was not done.'],
  ] as [Partial<ActionResultRow> | null, string, string][])('%o is thrown, with its code and words', async (row, code, message) => {
    const e = await caught(runOne(client(row), 'send', { id: 7, version: 3 }))
    expect(e).toBeInstanceOf(ActionRefused)
    expect(businessErrorOf(e)).toMatchObject({ code, message })
    expect((e as Error).message).toBe(message)
  })

  it('a button says it as an error and not its success; a confirmation stays open with it', async () => {
    const c = client({ status: 'skipped', reason: 'already queued', code: 'send.queued' })
    render(<QueryClientProvider client={new QueryClient()}><NoticeScope>
      <ActionButton done="Sent!" onAction={() => runOne(c, 'send', { id: 7, version: 3 })}>Send</ActionButton>
      <ConfirmAction open onOpenChange={() => {}} title="Sure?" done="Sent!" confirmLabel="Do it" onConfirm={() => runOne(c, 'send', { id: 7, version: 3 })} />
    </NoticeScope></QueryClientProvider>)
    await act(async () => { fireEvent.click(screen.getByText('Do it')) })
    expect(screen.getAllByRole('alert').some((x) => x.textContent?.includes('already queued'))).toBe(true)
    await act(async () => { fireEvent.click(screen.getByText('Send')) })
    expect(screen.getAllByText('already queued').length).toBeGreaterThan(1)
    expect(screen.queryByText('Sent!')).toBeNull()
  })
})

describe('actionDone', () => {
  const row = (x: Partial<ActionResultRow>) => ({ id: 7, status: 'done', ...x }) as ActionResultRow
  it('a problem is an error whatever else it says; then the note; then the words given', () => {
    expect(actionDone(row({ problem: 'the file failed', note: 'queued' }), 'Sent', 'k')).toEqual({ tone: 'error', text: 'The file failed', key: 'k' })
    expect(actionDone(row({ note: 'file 12 queued' }), 'Sent')).toEqual({ tone: 'success', text: 'file 12 queued' })
    expect(actionDone(row({}), 'Sent')).toEqual({ tone: 'success', text: 'Sent' })
  })
})

describe('what an action sends', () => {
  const meta = (sends: string | null): ListMeta => ({ version: 1, fields: [], searchable: false, default_sort: [], capabilities: {
    export: null, suggest: false, actions: [{ id: 'send', allowed: true, sends }] } })
  function Show({ c }: { c: { basePath: string; meta: () => Promise<ListMeta> } }) {
    const sends = useActionSends(c, 'send')
    return <><ActionButton sends={sends} onAction={async () => 'ok'}>Send</ActionButton>
      <ConfirmAction open onOpenChange={() => {}} title="Sure?" sends={sends} onConfirm={async () => {}} /></>
  }
  const show = (c: { basePath: string; meta: () => Promise<ListMeta> }) => render(<QueryClientProvider client={new QueryClient()}><NoticeScope><Show c={c} /></NoticeScope></QueryClientProvider>)

  it('is on the button and in the confirmation, as the server says it', async () => {
    show({ basePath: '/a', meta: async () => meta('a file to a partner') })
    expect(await screen.findByText('Sends a file to a partner.')).toBeInTheDocument()
    expect(screen.getByText('Send', { selector: 'button' })).toHaveAttribute('title', 'Sends a file to a partner')
  })

  it('nothing is said of one that sends nothing, or before the server has said', async () => {
    show({ basePath: '/b', meta: async () => meta(null) })
    await act(async () => {})
    expect(screen.queryByText(/^Sends/)).toBeNull()
    expect(screen.getByText('Send', { selector: 'button' })).not.toHaveAttribute('title')
  })
})
