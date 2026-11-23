import { useState } from 'react'
import { DayPicker, type DateRange } from 'react-day-picker'
import { CalendarRange } from 'lucide-react'
import 'react-day-picker/style.css'
import { Popover } from './Popover'
import { todayDate } from './dateValues'

/** A date range picker (shadcn's Calendar + Popover over react-day-picker). Values are plain dates "YYYY-MM-DD" in the
 *  viewer's own calendar — never through UTC (a date-only value read as UTC is a day early west of Greenwich). */
export interface DateRangeValue { from: string | null; to: string | null }

const toDate = (s: string | null) => (s ? new Date(`${s}T00:00:00`) : undefined)
const toText = (d: Date | undefined) => (d ? todayDate(d) : null)

export function DateRangeField({ value, onChange, label = 'Dates', placeholder = 'Any date' }: {
  value: DateRangeValue; onChange: (v: DateRangeValue) => void; label?: string; placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const selected: DateRange | undefined = value.from ? { from: toDate(value.from), to: toDate(value.to) } : undefined
  const text = value.from ? (value.to && value.to !== value.from ? `${value.from} – ${value.to}` : value.from) : placeholder
  return (
    <Popover open={open} onOpenChange={setOpen} align="start"
      trigger={
        <button type="button" aria-label={`${label}: ${text}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-2.5 text-sm text-neutral-700 hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <CalendarRange aria-hidden className="h-4 w-4" />{text}
        </button>
      }>
      <DayPicker mode="range" selected={selected} numberOfMonths={2} weekStartsOn={0}
        onSelect={(r) => onChange({ from: toText(r?.from), to: toText(r?.to ?? r?.from) })} />
      {value.from && (
        <button type="button" className="mt-1 text-xs text-primary-700 hover:underline" onClick={() => { onChange({ from: null, to: null }); setOpen(false) }}>
          Clear dates
        </button>
      )}
    </Popover>
  )
}
