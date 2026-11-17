import type { ElementType, ReactNode } from 'react'
import { useTourStop } from './useTourStop'

/** An element that is a tour stop (for a part whose own component doesn't take one): a wrapper with its
 *  help id and slot. */
export function TourMark({ help, slot, tourKey, as: As = 'span', className, children, role, 'aria-label': ariaLabel }: {
  help: string; slot: string; tourKey?: string; as?: ElementType; className?: string; children?: ReactNode; role?: string; 'aria-label'?: string
}) {
  const { attach } = useTourStop({ help, slot, key: tourKey })
  return <As ref={attach} data-help={help} className={className} role={role} aria-label={ariaLabel}>{children}</As>
}
