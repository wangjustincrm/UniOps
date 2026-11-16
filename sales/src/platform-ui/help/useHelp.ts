import { useContext } from 'react'
import { HelpContext } from './context'
import type { HelpEntry, HelpId } from './types'

/** The text for `id`, or undefined (no provider, no id, or no text: then nothing is shown). */
export function useHelp(id: HelpId | undefined | null): HelpEntry | undefined {
  const ctx = useContext(HelpContext)
  if (!id || !ctx) return undefined
  const entry = ctx.source(id)
  if (!entry) ctx.onMissing?.(id)
  return entry
}
