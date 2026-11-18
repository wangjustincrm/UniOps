/**
 * RecurrencePicker — select recurrence pattern for a booking.
 *
 * Emits SeriesSpec | null.
 * - None → null
 * - Daily / Weekly → { freq, interval, count? | until? }
 */
import { useState } from 'react'
import type { SeriesSpec } from '@/lib/types'
import { todayISO } from '@/lib/utils'

import { Input, Radio, Button } from '@uniops/shell'
interface Props {
  value: SeriesSpec | null
  onChange: (spec: SeriesSpec | null) => void
}

export function RecurrencePicker({ value, onChange }: Props) {
  // endMode is local state so clicking "By date" before entering a date doesn't snap back.
  // Initialised from value prop once (if parent already has an until date set).
  const [endMode, setEndModeState] = useState<'count' | 'until'>(
    value?.until ? 'until' : 'count',
  )

  const freq = value?.freq ?? null
  const interval = value?.interval ?? 1
  const count = value?.count ?? 4
  const until = value?.until ?? ''

  function setFreq(f: 'none' | 'daily' | 'weekly') {
    if (f === 'none') { onChange(null); return }
    // Preserve current end-mode settings
    onChange({
      freq: f,
      interval,
      ...(endMode === 'count' ? { count } : { until: until || undefined }),
    })
  }

  function setInterval(n: number) {
    if (!freq) return
    const v = Math.max(1, n)
    onChange({ freq, interval: v, ...(endMode === 'count' ? { count } : { until: until || undefined }) })
  }

  function setEndMode(mode: 'count' | 'until') {
    if (!freq) return
    setEndModeState(mode)
    // Clear the other field when switching modes so emitted spec is unambiguous
    if (mode === 'count') onChange({ freq, interval, count })
    else onChange({ freq, interval })  // until is empty until user types a date
  }

  function setCount(n: number) {
    if (!freq) return
    onChange({ freq, interval, count: Math.min(60, Math.max(2, n)) })
  }

  function setUntil(date: string) {
    if (!freq) return
    onChange({ freq, interval, until: date || undefined })
  }

  return (
    <div className="space-y-3">
      {/* Frequency selector */}
      <div className="flex gap-2">
        {(['none', 'daily', 'weekly'] as const).map((f) => {
          const label = f === 'none' ? 'None' : f === 'daily' ? 'Daily' : 'Weekly'
          const isActive = (f === 'none' && !freq) || f === freq
          return (
            <Button
              key={f}
              type="button"
              onClick={() => setFreq(f)}
              variant={isActive ? 'primary' : 'secondary'} size="sm" aria-pressed={isActive}
            >
              {label}
            </Button>
          )
        })}
      </div>

      {freq && (
        <div className="rounded-md border border-line bg-surface-subtle p-3 space-y-3">
          {/* Interval */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-fg-secondary">Every</span>
            <Input
              type="number"
              min={1}
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              className="w-16 text-center"
            />
            <span className="text-sm text-fg-secondary">
              {freq === 'daily' ? (interval === 1 ? 'day' : 'days') : (interval === 1 ? 'week' : 'weeks')}
            </span>
          </div>

          {/* End mode */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-fg-muted">Ends</p>

            <label className="flex items-center gap-2 cursor-pointer">
              <Radio
               
                name="recurrence-end-mode"
                checked={endMode === 'count'}
                onChange={() => setEndMode('count')}
                className="accent-[#085E5E]"
              />
              <span className="text-sm text-fg-secondary">After</span>
              <Input
                type="number"
                min={2}
                max={60}
                value={count}
                disabled={endMode !== 'count'}
                onChange={(e) => setCount(Number(e.target.value))}
                className="w-16 text-center"
              />
              <span className="text-sm text-fg-secondary">occurrences</span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer">
              <Radio
               
                name="recurrence-end-mode"
                checked={endMode === 'until'}
                onChange={() => setEndMode('until')}
                className="accent-[#085E5E]"
              />
              <span className="text-sm text-fg-secondary">By date</span>
              <Input
                type="date"
                value={until}
                disabled={endMode !== 'until'}
                min={todayISO()}
                onChange={(e) => setUntil(e.target.value)}
              />
            </label>
          </div>
        </div>
      )}
    </div>
  )
}
