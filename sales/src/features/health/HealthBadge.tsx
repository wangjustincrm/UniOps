import { Badge } from '@/platform-ui'
import type { HealthResult } from './healthApi'

const VARIANT = { PASSED: 'success', WARNING: 'warning', FAILED: 'danger' } as const

export function HealthBadge({ result }: { result: HealthResult }) {
  return <Badge variant={VARIANT[result]}>{result}</Badge>
}
