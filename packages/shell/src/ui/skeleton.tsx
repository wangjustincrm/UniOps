import { cn } from '../lib/cn'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn('h-4 rounded-md bg-neutral-100 animate-pulse', className)} />
  )
}

const WIDTHS = ['w-24', 'w-40', 'w-32', 'w-20', 'w-16', 'w-28', 'w-20', 'w-12']

export function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <Skeleton className={WIDTHS[i % WIDTHS.length]} />
        </td>
      ))}
    </tr>
  )
}
