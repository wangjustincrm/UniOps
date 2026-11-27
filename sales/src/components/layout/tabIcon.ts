import * as Lucide from 'lucide-react'
import type { ComponentType } from 'react'

/** A tab's icon by its lucide name (the route table's `tab.icon`), as the shell's own tab bar resolves it. */
export function tabIcon(name?: string): ComponentType<{ className?: string; 'aria-hidden'?: boolean }> | null {
  if (!name) return null
  return (Lucide as unknown as Record<string, ComponentType<{ className?: string }>>)[name] ?? null
}
