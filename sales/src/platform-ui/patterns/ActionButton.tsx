import { DisabledReason } from '../help'
import { cn } from '../lib/cn'
import { Button, type ButtonProps } from '../primitives/shell'
import { useContext } from 'react'
import { useCan } from './permissions'
import { ReadOnlyContext } from './recordNav'
import { useAction, type ActionOptions } from './useAction'

export interface ActionButtonProps extends Omit<ButtonProps, 'onClick'> {
  /** The permission key that allows this action; checked through PermissionProvider. */
  permission?: string
  /** Hidden (default) or shown disabled when the permission is missing. */
  whenDenied?: 'hide' | 'disable'
  onAction: () => unknown | Promise<unknown>
  pendingLabel?: string
  /** Inside a list or a sheet: what to say when it worked (its status row; a tone of its own if it says so);
   *  a failure is always said there. */
  done?: ActionOptions['done']
  /** Read again only these query keys (default: everything on screen). */
  invalidates?: ActionOptions['invalidates']
  /** Then, after it worked: e.g. close a sheet, show the result. */
  after?: ActionOptions['after']
  /** Business error codes → words, for a failure said in the status row. */
  errorMessages?: Record<string, string>
  /** Before the error text (e.g. "Download failed"). */
  failPrefix?: string
  /** Read again what is on screen after it worked (default true; a download changes nothing). */
  refreshes?: boolean
  /** Why it is disabled (with `disabled`): shown on tap, click or focus, and read as its description.
   *  A missing permission (whenDenied="disable") gives its own reason. */
  disabledReason?: string | null
  /** What it sends out of the company (useActionSends): said on the button (hover, focus, its description). */
  sends?: string
}

/** A button that cannot fire twice: aria-disabled while its action is pending (it keeps the focus), and
 *  guarded by a ref too, so two clicks in the same tick (before React has
 *  re-rendered it disabled) still run the action once. `type="button"` unless
 *  given, so it never submits a surrounding form. Inside a list or a sheet, a
 *  failure (and with `done`, a success) is said in its status row (useNotices). */
export function ActionButton({
  permission, whenDenied = 'hide', onAction, pendingLabel, disabled, children, done, errorMessages, failPrefix, refreshes, invalidates, after, disabledReason, sends, ...rest
}: ActionButtonProps) {
  const allowed = useCan(permission)
  // A record open read-only here (its own tab is open): what needs more than reading is greyed with where to go.
  const readOnly = useContext(ReadOnlyContext)
  const changes = !!permission && !permission.endsWith('.read')
  const { run, pending } = useAction({ done, errorMessages, failPrefix, refreshes, invalidates, after })
  if (!allowed && whenDenied === 'hide') return null

  const reason = !allowed ? "You don't have permission for this action." : readOnly && changes ? readOnly : disabled && !pending ? disabledReason : null
  if (reason) {
    // Not natively disabled: a disabled button can't be tapped or focused, so its reason couldn't be read.
    return (
      <DisabledReason reason={reason}>
        <Button type="button" {...rest} className={cn(rest.className, 'cursor-not-allowed opacity-50')}>{children}</Button>
      </DisabledReason>
    )
  }
  return (
    <Button
      type="button"
      title={sends ? `Sends ${sends}` : undefined}
      {...rest}
      // Pending is aria-disabled, not disabled: a disabled button drops the focus (to the page, or out of the dialog it
      // opens, which then cannot give it back); useAction's guard already runs it once.
      disabled={disabled || !allowed}
      aria-disabled={pending || undefined}
      aria-busy={pending || undefined}
      className={cn(rest.className, pending && 'cursor-wait opacity-70')}
      onClick={() => { if (!pending) void run(onAction) }}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </Button>
  )
}
