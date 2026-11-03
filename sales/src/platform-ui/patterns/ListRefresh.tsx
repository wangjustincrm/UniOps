import { RefreshCw } from 'lucide-react'
import { formatTime } from '../primitives/format'
import { IconButton } from '../primitives/IconButton'
import { Select } from '../primitives/Select'
import { AUTO_REFRESH } from '../resource/viewState'

const LABEL: Record<number, string> = { 0: 'Off', 30: 'Every 30s', 60: 'Every 1m', 300: 'Every 5m' }
/** A list's Refresh button (spinning while it fetches; its tooltip says when
 *  the rows were fetched) and its auto-refresh choice. */
export function ListRefresh({ onRefresh, fetching, updatedAt, auto, onAutoChange }: {
  onRefresh: () => void; fetching: boolean; updatedAt: number; auto: number; onAutoChange: (seconds: number) => void
}) {
  const when = updatedAt ? formatTime(updatedAt) : null
  return (
    <span className="inline-flex items-center gap-1">
      <IconButton label={when ? `Refresh · Updated ${when}` : 'Refresh'} icon={<RefreshCw className="h-4 w-4" />}
        pending={fetching} onClick={onRefresh} />
      <Select aria-label="Auto-refresh" className="h-8 w-28 text-xs" value={String(auto)}
        onChange={(v) => onAutoChange(Number(v ?? 0))}
        options={AUTO_REFRESH.map((s) => ({ value: String(s), label: s ? LABEL[s] : 'Auto: off' }))} />
    </span>
  )
}
