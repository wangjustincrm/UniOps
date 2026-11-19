import type { LucideIcon } from 'lucide-react'
import { cn } from '../lib/cn'
import { TONE, type TextTone } from './textStyle'

const SIZE = { 3: 'h-3 w-3', 3.5: 'h-3.5 w-3.5', 4: 'h-4 w-4', 5: 'h-5 w-5' } as const

/** An icon at one of the set sizes and tones (2026-11-18): the lucide component passed in, never sized by hand.
 *  With `label` it is read aloud; without, it is decoration (aria-hidden). */
export function Icon({ icon: I, size = 4, tone = 'default', label, className }: {
  icon: LucideIcon; size?: keyof typeof SIZE; tone?: TextTone; label?: string; className?: string
}) {
  return <I className={cn(SIZE[size], 'shrink-0', TONE[tone], className)} aria-hidden={label ? undefined : true} aria-label={label} />
}
