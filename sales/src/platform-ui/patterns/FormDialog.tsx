import type { ReactNode } from 'react'
import { Dialog } from '../primitives/Dialog'
import { ActionErrors } from './actionErrors'

/** THE dialog with a form (2026-11-18): a SchemaForm / MasterDetailForm inside (its Cancel / Save in the ActionBar,
 *  stuck to the bottom on a phone; its own errors on its fields and under it), or buttons that act: their failures
 *  are said inside it too. Closing asks about unsaved input. Pages never build a dialog by hand. */
export function FormDialog({ open, onOpenChange, title, description, children, wide }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  /** Wider, for a form with lines. */
  wide?: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description} className={wide ? 'max-w-2xl' : undefined}>
      <ActionErrors>{children}</ActionErrors>
    </Dialog>
  )
}
