import { Button, type ButtonProps } from '../primitives/shell'
import { useCan } from './permissions'
import { useAction } from './useAction'

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
  /** Before the error text (e.g. "Download failed"). */
  failPrefix?: string
}

/** A button that cannot fire twice: disabled while its action is pending, and
 *  guarded by a ref too, so two clicks in the same tick (before React has
 *  re-rendered it disabled) still run the action once. `type="button"` unless
 *  given, so it never submits a surrounding form. Inside a list or a sheet, a
 *  failure (and with `done`, a success) is said in its status row (useNotices). */
export function ActionButton({
  permission, whenDenied = 'hide', onAction, pendingLabel, disabled, children, done, errorMessages, failPrefix, ...rest
}: ActionButtonProps) {
  const allowed = useCan(permission)
  const { run, pending } = useAction({ done, errorMessages, failPrefix })
  if (!allowed && whenDenied === 'hide') return null

  return (
    <Button
      type="button"
      {...rest}
      disabled={disabled || pending || !allowed}
      aria-busy={pending || undefined}
      title={!allowed ? "You don't have permission for this action" : rest.title}
      onClick={() => { void run(onAction) }}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </Button>
  )
}
