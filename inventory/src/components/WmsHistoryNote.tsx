// When WMS figures begin (user, 2026-10-14): WMS went live in January 2025,
// but its stock history starts with the opening-stock import on 2025-02-03.
// The text comes from inventory-api so every page says the same thing.
import { Info } from 'lucide-react'
import type { WmsHistory } from '@/lib/wmsHistory'
import { cn } from '@/lib/utils'

/** `warn` = the page is showing a date before WMS history: say it loudly. */
export function WmsHistoryNote({ info, warn = false, className }: {
  info: WmsHistory | undefined
  warn?: boolean
  className?: string
}) {
  if (!info) return null
  return (
    <p className={cn(
      'flex items-start gap-1.5 text-xs',
      warn
        ? 'rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-warning-700'
        : 'text-neutral-400',
      className)}>
      <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{info.note}</span>
    </p>
  )
}
