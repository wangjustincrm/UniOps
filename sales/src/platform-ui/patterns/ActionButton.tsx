import { useRef, useState } from 'react'
import { Button, type ButtonProps } from '../primitives/shell'
import { useCan } from './permissions'
import { useNotices } from './notices'
import { errorText } from '../resource/businessError'

export interface ActionButtonProps extends Omit<ButtonProps, 'onClick'> {
  /** The permission key that allows this action; checked through PermissionProvider. */
  permission?: string
  /** Hidden (default) or shown disabled when the permission is missing. */
  whenDenied?: 'hide' | 'disable'
  onAction: () => unknown | Promise<unknown>
  pendingLabel?: string
  /** Inside a list or a sheet: what to say when it worked (its status row); a failure is always said there. */
  done?: string | ((result: unknown) => string | null)
  /** Business error codes → words, for a failure said in the status row. */
  errorMessages?: Record<string, string>
}

/** A button that cannot fire twice: disabled while its action is pending, and
 *  guarded by a ref too, so two clicks in the same tick (before React has
 *  re-rendered it disabled) still run the action once. `type="button"` unless
 *  given, so it never submits a surrounding form. Inside a list or a sheet, a
 *  failure (and with `done`, a success) is said in its status row (useNotices). */
export function ActionButton({
  permission, whenDenied = 'hide', onAction, pendingLabel, disabled, children, done, errorMessages, ...rest
}: ActionButtonProps) {
  const allowed = useCan(permission)
  const notices = useNotices()
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  if (!allowed && whenDenied === 'hide') return null

  const run = async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    try {
      const result = await onAction()
      const said = typeof done === 'function' ? done(result) : done
      if (said) notices.notify({ tone: 'success', text: said })
    } catch (e) {
      // In a list or a sheet the failure is said in its status row; elsewhere it goes on as before.
      if (!notices.inScope) throw e
      notices.notify({ tone: 'error', text: errorText(e, errorMessages) })
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
