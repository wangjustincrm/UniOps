// Test helpers for the library's controls (imported by tests only).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { expect } from 'vitest'

// Driving the platform's controls in tests, the way a keyboard user does.

/** Opens a Select (Enter on its trigger) and picks the option with this name. */
export async function chooseOption(trigger: HTMLElement, label: string) {
  fireEvent.keyDown(trigger, { key: 'ArrowDown' })
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

/** Chooses a record sheet's tab by its label (its count and dot may follow), the way a pointer does (Radix: on mouse down). */
export async function openTab(container: HTMLElement, label: string) {
  const tab = await within(container).findByRole('tab', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
  fireEvent.mouseDown(tab, { button: 0 })
  await waitFor(() => expect(tab).toHaveAttribute('aria-selected', 'true'))
  return tab
}

/** The current notice in the status row inside `root` (a list page, a sheet): its chip, or an empty node. */
export function noticeIn(root: ParentNode = document): Element {
  return root.querySelector('[data-status-bar] [data-tone]') ?? document.createElement('div')
}

/** Opens a record's "⋯" (its action bar's More actions, by keyboard) and gives the menu. */
export async function openMoreActions(scope: HTMLElement = document.body) {
  fireEvent.keyDown((await within(scope).findAllByRole('button', { name: 'More actions' }))[0], { key: 'Enter' })
  return screen.findByRole('menu')
}

/** A record's "⋯" does not offer this action (opened, looked at, closed again). */
export async function expectNoMoreAction(label: string | RegExp, scope: HTMLElement = document.body) {
  const menu = await openMoreActions(scope)
  expect(within(menu).queryByRole('menuitem', { name: label })).toBeNull()
  fireEvent.keyDown(menu, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
}

/** Picks an action from a record's "⋯". */
export async function chooseMoreAction(label: string | RegExp, scope: HTMLElement = document.body) {
  fireEvent.click(within(await openMoreActions(scope)).getByRole('menuitem', { name: label }))
}

/** Opens a list's View button (at the right end of its toolbar) and gives the view's own actions (Save view, …). */
export async function openViewMenu() {
  fireEvent.click(await screen.findByRole('button', { name: /^View:/ }))
  return screen.findByRole('group', { name: 'This view' })
}

/** Picks one of the view's actions from the View button's menu. */
export async function chooseViewAction(name: string | RegExp) {
  fireEvent.click(within(await openViewMenu()).getByRole('button', { name }))
}
