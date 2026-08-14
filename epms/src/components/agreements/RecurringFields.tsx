import type { JSX } from 'react'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import type { RecurringType } from '@/services/agreement'

export interface RecurringFieldsValue {
  recurringType: RecurringType | ''
  expectedInvoiceDay: string
  scheduleStartDate: string
  anchorMonth: string
  amountPerPeriod: string
  tolerancePct: string
  overdueAfterDays: string
}

// Backend default for a fresh recurring agreement — see AgreementCreatePage's
// initial state and epms-api/app/models/agreement.py's server_default=7.
export const EMPTY_RECURRING_FIELDS: RecurringFieldsValue = {
  recurringType: '',
  expectedInvoiceDay: '',
  scheduleStartDate: '',
  anchorMonth: '',
  amountPerPeriod: '',
  tolerancePct: '',
  overdueAfterDays: '7',
}

const WEEKDAYS: { value: number; label: string }[] = [
  { value: 1, label: 'Monday' },
  { value: 2, label: 'Tuesday' },
  { value: 3, label: 'Wednesday' },
  { value: 4, label: 'Thursday' },
  { value: 5, label: 'Friday' },
  { value: 6, label: 'Saturday' },
  { value: 7, label: 'Sunday' },
]

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const ANCHORED_TYPES: RecurringType[] = ['quarterly', 'yearly']

const selectClass =
  'h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400'

/**
 * Cycle / expected-invoice-day / anchor-month / amount-per-period /
 * tolerance / overdue-after-days for a `recurring` agreement. Rendered only
 * by the parent when `agreement_type === 'recurring'` — mirrors the backend
 * rejection of these fields on any other type
 * (epms-api/app/schemas/agreement.py::validate_recurrence).
 */
export function RecurringFields(props: {
  value: RecurringFieldsValue
  validFrom: string
  onChange: (next: RecurringFieldsValue) => void
  disabled?: boolean
}): JSX.Element {
  const { value, validFrom, onChange, disabled } = props
  const isAnchored = ANCHORED_TYPES.includes(value.recurringType as RecurringType)

  const set = (patch: Partial<RecurringFieldsValue>) => onChange({ ...value, ...patch })

  const handleCycleChange = (next: RecurringType | '') => {
    const patch: Partial<RecurringFieldsValue> = { recurringType: next }
    const nextIsAnchored = ANCHORED_TYPES.includes(next as RecurringType)
    if (nextIsAnchored) {
      // Prefill from valid_from's month on the switch INTO quarterly/yearly,
      // but only if the user hasn't already set one — real billing anchors
      // (Feb/May/Aug/Nov) frequently diverge from the contract signature
      // date, so this is a starting point, not an authority. validFrom is
      // "YYYY-MM-DD"; slice avoids a Date() timezone round-trip.
      if (!value.anchorMonth && validFrom) {
        const month = Number(validFrom.slice(5, 7))
        if (month >= 1 && month <= 12) patch.anchorMonth = String(month)
      }
    } else {
      patch.anchorMonth = ''
    }
    set(patch)
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">
            Cycle <span className="text-danger-600">*</span>
          </label>
          <select
            value={value.recurringType}
            disabled={disabled}
            onChange={(e) => handleCycleChange(e.target.value as RecurringType | '')}
            className={selectClass}
          >
            <option value="">Select cycle…</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">
            Expected invoice day <span className="text-danger-600">*</span>
          </label>
          {value.recurringType === 'weekly' ? (
            <select
              value={value.expectedInvoiceDay}
              disabled={disabled}
              onChange={(e) => set({ expectedInvoiceDay: e.target.value })}
              className={selectClass}
            >
              <option value="">Select weekday…</option>
              {WEEKDAYS.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          ) : (
            <>
              <Input
                type="number"
                min={1}
                max={31}
                disabled={disabled}
                value={value.expectedInvoiceDay}
                onChange={(e) => set({ expectedInvoiceDay: e.target.value })}
              />
              <p className="text-xs text-neutral-500">Clamped to the last day in shorter months</p>
            </>
          )}
        </div>
      </div>

      {/* Schedule start (ag08). An agreement is routinely entered into the
          system a year or two into its life: generating from Valid From then
          produces rows for invoices that were paid outside this system and
          will never arrive here — they sit unclaimed, the overdue sweep flips
          them, and the owner is emailed about them daily. */}
      <FormField
        label="Generate schedule from"
        htmlFor="scheduleStartDate"
        hint="Leave blank to start at Valid From. Set it when the agreement has been running outside the system — earlier periods are simply not created. The cycle itself is unaffected: labels and anchors still follow the contract."
      >
        <Input
          id="scheduleStartDate"
          type="date"
          min={validFrom || undefined}
          disabled={disabled}
          value={value.scheduleStartDate}
          onChange={(e) => set({ scheduleStartDate: e.target.value })}
        />
      </FormField>

      {isAnchored && (
        <FormField
          label="Anchor month"
          required
          htmlFor="anchorMonth"
          hint="Quarterly billing often runs Feb/May/Aug/Nov rather than the calendar quarters"
        >
          <select
            id="anchorMonth"
            value={value.anchorMonth}
            disabled={disabled}
            onChange={(e) => set({ anchorMonth: e.target.value })}
            className={selectClass}
          >
            <option value="">Select month…</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
        </FormField>
      )}

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        <FormField
          label="Expected amount per period"
          htmlFor="amountPerPeriod"
          hint="The contract price BEFORE tax — invoices are compared on their pre-tax amount. Leave blank for usage-based bills."
        >
          <Input
            id="amountPerPeriod"
            type="number"
            min={0}
            step="0.01"
            disabled={disabled}
            value={value.amountPerPeriod}
            onChange={(e) => {
              const next = e.target.value
              // Tolerance is meaningless without an amount to compare
              // against — clear it alongside disabling the field so a
              // leftover value from before the amount was cleared can't
              // sneak into the submit payload.
              set({ amountPerPeriod: next, tolerancePct: next ? value.tolerancePct : '' })
            }}
          />
        </FormField>

        <FormField
          label="Tolerance %"
          htmlFor="tolerancePct"
          hint={
            !value.amountPerPeriod
              ? 'Set an expected amount to enable a tolerance'
              : 'Leave blank to accept any amount. 0 means the pre-tax amount must match to the cent.'
          }
        >
          <Input
            id="tolerancePct"
            type="number"
            min={0}
            max={100}
            step="0.01"
            disabled={disabled || !value.amountPerPeriod}
            value={value.tolerancePct}
            onChange={(e) => set({ tolerancePct: e.target.value })}
          />
        </FormField>

        <FormField label="Overdue after (days)" htmlFor="overdueAfterDays">
          <Input
            id="overdueAfterDays"
            type="number"
            min={0}
            max={365}
            disabled={disabled}
            value={value.overdueAfterDays}
            onChange={(e) => set({ overdueAfterDays: e.target.value })}
          />
        </FormField>
      </div>
    </div>
  )
}
