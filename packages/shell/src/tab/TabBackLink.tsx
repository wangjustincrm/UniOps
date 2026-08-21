import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { useBackTab } from './useBackTab'
import type { RouteDef } from './types'

export interface TabBackLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  routes: RouteDef[]
  /** Module-relative destination, e.g. '/po' or '/gr/123'. */
  to: string
}

/**
 * A "back to the parent" link that closes the tab it is clicked in.
 *
 * Renders a real anchor so the destination is visible in the status bar and
 * modified clicks (ctrl/cmd/middle) still open a normal browser tab; a plain
 * left click is intercepted and routed through {@link useBackTab}.
 *
 * Apps normally re-export a thin wrapper that binds their own route table, so
 * pages can write `<BackLink to="/po">Back to PO List</BackLink>`.
 */
export function TabBackLink({ routes, to, onClick, children, ...rest }: TabBackLinkProps) {
  const back = useBackTab(routes)

  const handleClick = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e)
    if (e.defaultPrevented) return
    // Let the browser handle "open in a new window/tab" gestures.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    back(to)
  }

  return (
    <a href={to} onClick={handleClick} {...rest}>
      {children}
    </a>
  )
}
