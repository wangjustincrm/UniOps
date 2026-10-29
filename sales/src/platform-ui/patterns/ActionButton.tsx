import { useRef, useState } from 'react'
import { Button, type ButtonProps } from '../primitives/shell'
import { useCan } from './permissions'

export interface ActionButtonProps extends Omit<ButtonProps, 'onClick'> {
  /** The permission key that allows this action; checked through PermissionProvider. */
  permission?: string
  /** Hidden (default) or shown disabled when the permission is missing. */
  whenDenied?: 'hide' | 'disable'
  onAction: () => unknown | Promise<unknown>
  pendingLabel?: string
}

/** A button that cannot fire twice: disabled while its action is pending, and
 *  guarded by a ref too, so two clicks in the same tick (before React has
 *  re-rendered it disabled) still run the action once. `type="button"` unless
 *  given, so it never submits a surrounding form. */
export function ActionButton({
  permission, whenDenied = 'hide', onAction, pendingLabel, disabled, children, ...rest
}: ActionButtonProps) {
  const allowed = useCan(permission)
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  if (!allowed && whenDenied === 'hide') return null

  const run = async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    try {
      await onAction()
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <Button
      type="button"
      {...rest}
      disabled={disabled || pending || !allowed}
      aria-busy={pending || undefined}
      title={!allowed ? "You don't have permission for this action" : rest.title}
      onClick={run}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </Button>
  )
}
