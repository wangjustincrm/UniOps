import { useCallback, type ElementType, type ReactNode } from 'react'
import { useTourStop } from './useTourStop'

/** An element that is a tour stop (for a part whose own component doesn't take one): a wrapper with its
 *  help id and slot. */
export function TourMark({ help, slot, tourKey, also, as: As = 'span', className, children, role, 'aria-label': ariaLabel }: {
  help: string; slot: string; tourKey?: string; as?: ElementType; className?: string; children?: ReactNode; role?: string; 'aria-label'?: string
  /** A second tip on the same element (its own slot and text), e.g. "Find by status" on Search on any field. */
  also?: { slot: string; text: string }
}) {
  const { attach } = useTourStop({ help, slot, key: tourKey })
  const { attach: attachAlso } = useTourStop({ help, slot: also?.slot ?? '', text: also?.text })
  const both = useCallback((el: HTMLElement | null) => {
    const a = attach(el)
    const b = also ? attachAlso(el) : undefined
    return () => { a?.(); b?.() }
  }, [attach, attachAlso, also])
  return <As ref={both} data-help={help} className={className} role={role} aria-label={ariaLabel}>{children}</As>
}
