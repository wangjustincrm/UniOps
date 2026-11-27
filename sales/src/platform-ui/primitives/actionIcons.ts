import { createElement, type ReactNode } from 'react'
import { Download, Plus, type LucideIcon } from 'lucide-react'

/** One icon per action in every data table (the user, 2026-11-27): Export is Download, New (create) is Plus — set here
 *  once; a list overrides it by its own option (ListPage exportIcon / create.icon, CrudPage exportIcon / createIcon),
 *  never by placing another icon by hand. */
export const ACTION_ICONS = { export: Download, create: Plus } as const satisfies Record<string, LucideIcon>

/** An action's icon as a button shows it before its label: the default, the list's own, or none (false). */
export function actionIcon(kind: keyof typeof ACTION_ICONS, own?: ReactNode | false, className = 'mr-1 h-4 w-4'): ReactNode {
  if (own === false) return null
  if (own !== undefined) return own
  return createElement(ACTION_ICONS[kind], { 'aria-hidden': true, 'data-icon': kind, className } as Record<string, unknown>)
}
