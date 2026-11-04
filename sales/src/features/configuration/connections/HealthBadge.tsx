import { Badge, Tooltip } from '@/platform-ui'
import { connectionHealth } from './connectionHealth'
import type { ConnectionRead } from './connectionsApi'

/** The connection's health as one badge; the tooltip says what to do. */
export function HealthBadge({ c }: { c: ConnectionRead }) {
  const h = connectionHealth(c)
  return <Tooltip tip={h.tip}><span><Badge variant={h.tone}>{h.label}</Badge></span></Tooltip>
}
