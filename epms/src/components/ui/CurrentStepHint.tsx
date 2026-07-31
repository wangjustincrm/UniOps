import type { CurrentStep } from '@/types'

function daysSince(iso: string): number {
  const ms = Date.now() - new Date(iso).getTime()
  return Math.max(0, Math.floor(ms / 86_400_000))
}

/** Muted subtext shown under the status badge for in-review rows:
 *  "GM / OPM · Zhang San · 3d"  or  "Finance BP · 3d" (role pool, no assignee). */
export function CurrentStepHint({ current_step }: { current_step?: CurrentStep | null }) {
  if (!current_step) return null
  const { label, approver_name, since } = current_step
  const parts = [label]
  if (approver_name) parts.push(approver_name)
  parts.push(`${daysSince(since)}d`)
  return (
    <div
      className="mt-1 text-[11px] leading-tight text-neutral-500 whitespace-nowrap"
      title={`Awaiting ${label}${approver_name ? ` (${approver_name})` : ''} since ${new Date(since).toLocaleString()}`}
    >
      {parts.join(' · ')}
    </div>
  )
}
