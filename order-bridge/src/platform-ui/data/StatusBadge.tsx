import { Badge } from '@uniops/shell'

export type BadgeVariant = 'neutral' | 'warning' | 'info' | 'success' | 'danger' | 'dark'

export interface StatusStyle {
  variant: BadgeVariant
  label?: string
}

/** A status shown through a map the business code supplies
 *  (status → variant and optional label). Unknown values render neutral. */
export function StatusBadge({
  status, map, className,
}: {
  status: string | null | undefined
  map: Record<string, StatusStyle>
  className?: string
}) {
  if (!status) return <span className="text-neutral-400">—</span>
  const style = map[status]
  return (
    <Badge variant={style?.variant ?? 'neutral'} className={className}>
      {style?.label ?? status}
    </Badge>
  )
}
