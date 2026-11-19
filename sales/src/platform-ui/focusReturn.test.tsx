import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useState } from 'react'
import { ActionButton, PermissionProvider } from './index'
import { Sheet } from './primitives/Sheet'
import { Dialog } from './primitives/Dialog'

// A closed sheet or dialog gives the focus back to what opened it (step 6, WCAG 2.4.3).

function Opens({ as }: { as: 'sheet' | 'dialog' }) {
  const [open, setOpen] = useState(false)
  const Panel = as === 'sheet' ? Sheet : Dialog
  return (
    <PermissionProvider can={() => true}>
      <ActionButton onAction={() => setOpen(true)}>Open it</ActionButton>
      <Panel open={open} onOpenChange={setOpen} title="Panel"><button type="button">Inside</button></Panel>
    </PermissionProvider>
  )
}

describe('focus return', () => {
  for (const as of ['sheet', 'dialog'] as const) {
    it(`a ${as} opened by an ActionButton gives the focus back to it on Esc`, async () => {
      render(<Opens as={as} />)
      const opener = screen.getByRole('button', { name: 'Open it' })
      opener.focus()
      await act(async () => { fireEvent.click(opener) })
      const panel = await screen.findByRole('dialog')
      await waitFor(() => expect(panel.contains(document.activeElement)).toBe(true))
      fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      await waitFor(() => expect(screen.getByRole('button', { name: 'Open it' })).toHaveFocus())
    })
  }
})
