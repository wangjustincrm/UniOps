// Test helpers for the library's controls (imported by tests only).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { expect } from 'vitest'

// Driving the platform's controls in tests, the way a keyboard user does.

/** Opens a Select (Enter on its trigger) and picks the option with this name. */
export async function chooseOption(trigger: HTMLElement, label: string) {
  fireEvent.keyDown(trigger, { key: 'Enter' })
  fireEvent.click(await screen.findByRole('option', { name: label }))
  await settled(() => screen.queryByRole('listbox'))
}

/** The list has closed and Radix has put the focus back (a tick later). */
async function settled(list: () => Element | null) {
  await waitFor(() => expect(list()).toBeNull())
  await act(() => new Promise((r) => setTimeout(r, 0)))
}

/** Opens an Autocomplete, types `query` and picks the option with this name. */
export async function pickSuggestion(trigger: HTMLElement, query: string, label: string | RegExp) {
  fireEvent.click(trigger)
  const listId = trigger.getAttribute('aria-controls')!
  const listbox = await waitFor(() => {
    const el = document.getElementById(listId)
    if (!el) throw new Error(`no listbox #${listId}`)
    return el
  })
  const search = screen.getAllByRole('textbox').find((el) => el.getAttribute('aria-controls') === listId)!
  fireEvent.change(search, { target: { value: query } })
  fireEvent.click(await within(listbox).findByRole('option', { name: label }))
  // Like a user: the list closes and the focus is back on the trigger
  // before the next control is used.
  await settled(() => document.getElementById(listId))
}
