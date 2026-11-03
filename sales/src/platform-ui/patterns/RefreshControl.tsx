import { Check, ChevronDown, RefreshCw } from 'lucide-react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { TipButton } from '../primitives/TipButton'
import { formatTime } from '../primitives/format'
import { cn } from '../lib/cn'
import { AUTO_REFRESH } from '../resource/viewState'
import type { RefreshState } from './useRefresh'

const SHORT: Record<number, string> = { 30: '30', 60: '1m', 300: '5m' }
const LONG: Record<number, string> = { 0: 'Off', 30: 'Every 30s', 60: 'Every 1m', 300: 'Every 5m' }

/** THE refresh control (lists and record sheets): a split button. The icon
 *  refreshes and spins while a refresh runs — a click and an auto-refresh
 *  tick look the same; with auto on, its interval sits on the icon ("30",
 *  "1m") and the button is tinted. The chevron (lists only) chooses Off / 30s
 *  / 1m / 5m. Beside it, briefly, what the last refresh found. */
export function RefreshControl({ state }: { state: RefreshState }) {
  const { auto, busy, setAuto } = state
  const when = state.updatedAt ? formatTime(state.updatedAt) : null
  const tip = ['Refresh', auto ? `auto every ${LONG[auto]?.replace('Every ', '') ?? `${auto}s`}` : null, when && `updated ${when}`]
    .filter(Boolean).join(' · ')
  const tone = state.message?.tone
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-live="polite" data-refresh-message
        className={cn('max-w-[16rem] truncate text-xs transition-opacity', state.message ? 'opacity-100' : 'opacity-0',
          tone === 'danger' ? 'text-danger-600' : tone === 'muted' ? 'text-neutral-500' : 'text-primary-700')}>
        {state.message?.text ?? ''}
      </span>
      <span className={cn('inline-flex h-8 items-stretch rounded-lg border', auto ? 'border-primary-200 bg-primary-50' : 'border-neutral-200 bg-white')}>
        <TipButton label={tip} aria-busy={busy || undefined} disabled={busy}
          onClick={() => { void state.refresh() }}
          className={cn('relative inline-flex w-8 items-center justify-center text-neutral-600 hover:text-neutral-900',
            setAuto ? 'rounded-l-lg' : 'rounded-lg', auto && 'text-primary-700',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 disabled:cursor-progress')}>
          <RefreshCw aria-hidden className={cn('h-4 w-4', busy && 'animate-spin motion-reduce:animate-none')} />
          {!!auto && (
            <span data-auto-interval className="absolute -bottom-1 -right-1 rounded bg-primary-600 px-0.5 text-[9px] font-semibold leading-3 text-white">
              {SHORT[auto] ?? auto}
            </span>
          )}
        </TipButton>
        {setAuto && (
          <DropdownMenu label="Auto-refresh" align="end"
            items={AUTO_REFRESH.map((s) => ({
              label: LONG[s], onSelect: () => setAuto(s),
              icon: <Check aria-hidden className={cn('h-3.5 w-3.5', s === auto ? 'opacity-100' : 'opacity-0')} />,
            }))}
            trigger={
              <button type="button" aria-label="Auto-refresh" className={cn('inline-flex w-5 items-center justify-center rounded-r-lg border-l text-neutral-500 hover:text-neutral-900',
                auto ? 'border-primary-200' : 'border-neutral-200', 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600')}>
                <ChevronDown aria-hidden className="h-3.5 w-3.5" />
              </button>
            } />
        )}
      </span>
    </span>
  )
}
