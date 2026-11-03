import { cn } from '../lib/cn'

/** A thin indeterminate bar along the top edge of a card or under a header
 *  while data is fetched, with a polite "Loading…" for screen readers. With
 *  reduced motion it stands still (a partial bar). Place it in a relative box. */
export function BusyBar({ active, className }: { active: boolean; className?: string }) {
  if (!active) return null
  return (
    <>
      <div aria-hidden data-busy-bar className={cn('pointer-events-none absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden bg-primary-100', className)}>
        <div className="h-full w-1/3 bg-primary-600 animate-[platform-busy_1.1s_ease-in-out_infinite] motion-reduce:ml-[33%] motion-reduce:animate-none" />
      </div>
      <span role="status" aria-live="polite" className="sr-only">Loading…</span>
    </>
  )
}
