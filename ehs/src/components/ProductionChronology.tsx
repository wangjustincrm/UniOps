/**
 * ★ UAT 13: the Production Chronology, on a food-safety near miss's
 * investigation, under What happened.
 *
 * Kirk: "create a spreadsheet like, detailed production chronology, including
 * the production start time, the point at which the deviation was detected,
 * containment actions taken, production end time, affected quantity,
 * inspection activities, and current product status … Make this field
 * optional to complete." A row per event in time order, and the two things
 * that are a state rather than an event — how much was affected and where the
 * product is now — under the table.
 */
import { Plus, Trash2 } from 'lucide-react'

export interface ChronologyRow { at: string | null; stage: string; detail: string; quantity: string | null }
export interface Chronology {
  rows: ChronologyRow[]; affected_quantity: string | null; product_status: string | null
}
export const EMPTY_CHRONOLOGY: Chronology = { rows: [], affected_quantity: null, product_status: null }

export const STAGES: { code: string; label: string }[] = [
  { code: 'start', label: 'Production started' },
  { code: 'deviation', label: 'Deviation detected' },
  { code: 'containment', label: 'Containment action' },
  { code: 'inspection', label: 'Inspection' },
  { code: 'end', label: 'Production ended' },
  { code: 'other', label: 'Other' },
]

const cell = 'min-h-[38px] w-full rounded-md border border-neutral-200 bg-white px-2 text-sm'
const TH = 'px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500'

export function ProductionChronology({ value, onChange }: {
  value: Chronology; onChange: (next: Chronology) => void
}) {
  const rows = value.rows
  const setRow = (i: number, patch: Partial<ChronologyRow>) =>
    onChange({ ...value, rows: rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
  const add = () => {
    // The first row is usually when production started; after that, "other".
    const stage = rows.length === 0 ? 'start' : 'other'
    onChange({ ...value, rows: [...rows, { at: null, stage, detail: '', quantity: null }] })
  }
  return (
    <div className="mt-3 rounded-lg border border-neutral-200 p-3">
      <p className="text-sm font-semibold text-neutral-800">Production Chronology <span className="font-normal text-neutral-500">(optional)</span></p>
      <p className="mb-2 text-xs text-neutral-500">
        What happened on the line, in order: when production started, when the deviation was
        detected, what was done to contain it, inspections, when production ended.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr><th className={`${TH} w-44`}>When</th><th className={`${TH} w-44`}>What</th>
                <th className={TH}>Detail</th><th className={`${TH} w-28`}>Quantity</th><th className="w-8" /></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="align-top">
                <td className="p-1">
                  <input type="datetime-local" className={cell} value={r.at ?? ''} aria-label="When"
                         onChange={(e) => setRow(i, { at: e.target.value || null })} />
                </td>
                <td className="p-1">
                  <select className={cell} value={r.stage} aria-label="What"
                          onChange={(e) => setRow(i, { stage: e.target.value })}>
                    {STAGES.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
                  </select>
                </td>
                <td className="p-1">
                  <textarea rows={1} className={`${cell} py-1.5`} value={r.detail} aria-label="Detail"
                            onChange={(e) => setRow(i, { detail: e.target.value })} />
                </td>
                <td className="p-1">
                  <input className={cell} value={r.quantity ?? ''} aria-label="Quantity" placeholder="e.g. 40 bags"
                         onChange={(e) => setRow(i, { quantity: e.target.value || null })} />
                </td>
                <td className="p-1">
                  <button type="button" aria-label="Remove this line"
                          onClick={() => onChange({ ...value, rows: rows.filter((_, j) => j !== i) })}
                          className="flex min-h-[38px] items-center px-1 text-neutral-400 hover:text-danger-600">
                    <Trash2 className="h-4 w-4" aria-hidden />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" onClick={add}
              className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline">
        <Plus className="h-3.5 w-3.5" aria-hidden /> Add a line
      </button>
      <div className="mt-3 grid gap-2 sm:grid-cols-[14rem_minmax(0,1fr)]">
        <label className="text-xs font-medium text-neutral-700">
          Affected quantity
          <input className={`${cell} mt-1`} value={value.affected_quantity ?? ''}
                 onChange={(e) => onChange({ ...value, affected_quantity: e.target.value || null })} />
        </label>
        <label className="text-xs font-medium text-neutral-700">
          Current product status
          <input className={`${cell} mt-1`} value={value.product_status ?? ''}
                 placeholder="On hold, released, reworked, destroyed…"
                 onChange={(e) => onChange({ ...value, product_status: e.target.value || null })} />
        </label>
      </div>
    </div>
  )
}
