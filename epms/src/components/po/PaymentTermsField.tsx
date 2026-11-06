import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { MAX_NET_DAYS, PRESET_PAYMENT_TERMS, customDaysToTerms, isPresetTerms, netDays } from '@/lib/paymentTerms'

const CUSTOM = '__custom__'

/**
 * A PO's payment terms (PRD §3.12.1 "defaulted from vendor; overridable",
 * ISS-0087): the vendor form's presets plus Custom (Net N days), same codes
 * as vendors.payment_terms. `value` is a code (`net30`, `cod`, …) or '' while
 * a custom day count is not valid yet.
 */
export function PaymentTermsField({ value, onChange, hint, invalid }: {
  value: string
  onChange: (terms: string) => void
  hint?: string | null
  invalid?: boolean
}) {
  const isCustom = !!value && !isPresetTerms(value)
  const [mode, setMode] = useState<string>(isCustom ? CUSTOM : value)
  const [days, setDays] = useState<string>(isCustom ? String(netDays(value) ?? '') : '')
  const [error, setError] = useState<string | null>(null)

  // Follow a value set from outside (vendor picked, PO loaded).
  useEffect(() => {
    if (!value) return
    if (isPresetTerms(value)) { setMode(value); setError(null) }
    else { setMode(CUSTOM); setDays(String(netDays(value) ?? '')); setError(null) }
  }, [value])

  const inputCls = 'h-10 w-full rounded-lg border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2">
        <select
          aria-label="Payment terms"
          className={cn(inputCls, invalid || error ? 'border-danger-400' : 'border-neutral-300')}
          value={mode}
          onChange={(e) => {
            const v = e.target.value
            setMode(v)
            if (v === CUSTOM) { setDays(''); onChange('') } else { setError(null); onChange(v) }
          }}
        >
          {!mode && <option value="">Select…</option>}
          {PRESET_PAYMENT_TERMS.map(({ value: v, label }) => <option key={v} value={v}>{label}</option>)}
          <option value={CUSTOM}>Custom (Net N days)…</option>
        </select>
        {mode === CUSTOM && (
          <div className="relative w-32 shrink-0">
            <input
              type="number" min={1} max={MAX_NET_DAYS} step={1} inputMode="numeric" aria-label="Net days"
              className={cn(inputCls, 'pr-11', error ? 'border-danger-400' : 'border-neutral-300')}
              value={days}
              placeholder="21"
              onChange={(e) => {
                setDays(e.target.value)
                const r = customDaysToTerms(e.target.value)
                if ('terms' in r) { setError(null); onChange(r.terms) } else { setError(r.error); onChange('') }
              }}
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-neutral-400">days</span>
          </div>
        )}
      </div>
      {error ? <p className="text-xs text-danger-600">{error}</p>
        : hint ? <p className="text-xs text-neutral-400">{hint}</p> : null}
    </div>
  )
}
