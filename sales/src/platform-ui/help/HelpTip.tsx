import * as RP from '@radix-ui/react-popover'
import { CircleHelp } from 'lucide-react'
import type { ReactNode } from 'react'
import { cx } from './cx'
import { HelpContent } from './HelpContent'
import { useHelp } from './useHelp'
import { useTourStop } from './useTourStop'
import type { HelpEntry, HelpId } from './types'

/** Help next to what it explains. Opens on click / tap / Enter (a phone has no hover) and closes on
 *  Esc or a click outside. By `id` (the provider's text) or with an `entry`; with no text it renders
 *  nothing (or just its children). Without children: a small ? icon. With children: they are the
 *  trigger (e.g. a status badge you tap to read what it means). `data-help` marks it for a tour. */
export function HelpTip({ id, entry, label, children, className, tourSlot, tourKey }: {
  id?: HelpId; entry?: HelpEntry; label?: string; children?: ReactNode; className?: string
  /** Makes it a stop of its page's or record's tour: its slot (a name in the slot table); one stop per `tourKey`. */
  tourSlot?: string; tourKey?: string
}) {
  const found = useHelp(entry ? undefined : id)
  const stop = useTourStop({ help: id ?? '', slot: tourSlot ?? '', key: tourKey })
  const help = entry ?? found
  if (!help) return <>{children ?? null}</>
  const name = label ?? (help.title ? `What "${help.title}" means` : 'Help')
  return (
    <RP.Root>
      <RP.Trigger asChild>
        {children ? (
          <button ref={tourSlot && id ? stop.attach : undefined} type="button" data-help={id} aria-label={name}
            className={cx('inline-flex cursor-help items-center rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary-600', className)}>
            {children}
          </button>
        ) : (
          <button type="button" data-help={id} aria-label={name}
            className={cx('inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full align-middle text-neutral-400 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600', className)}>
            <CircleHelp className="h-3.5 w-3.5" aria-hidden />
          </button>
        )}
      </RP.Trigger>
      <HelpContent entry={help} />
    </RP.Root>
  )
}
