import { useMemo } from 'react'
import type { ChildSchema } from '@/services/adminApi'

export interface LineRow { id?: string; [k: string]: unknown }

interface Props {
  child: ChildSchema
  rows: LineRow[]
  onChange: (rows: LineRow[]) => void
}

const dec = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }

export function LineItemsEditor({ child, rows, onChange }: Props) {
  const editable = child.fields.filter((f) => f.editable)

  const set = (i: number, name: string, value: string) => {
    const next = rows.map((r, j) => (j === i ? { ...r, [name]: value } : r))
    onChange(next)
  }
  const addRow = () => onChange([...rows, Object.fromEntries(editable.map((f) => [f.name, ''])) as LineRow])
  const delRow = (i: number) => onChange(rows.filter((_, j) => j !== i))

  const subtotal = useMemo(
    () => rows.reduce((s, r) => s + dec(r.qty) * dec(r.unit_price), 0), [rows])

  return (
    <div className="rounded-lg border border-neutral-200">
      <div className="flex items-center justify-between border-b border-neutral-100 px-3 py-2">
        <span className="text-sm font-medium">{child.table_label}</span>
        <button type="button" onClick={addRow}
          className="rounded bg-primary-600 px-2 py-1 text-xs font-semibold text-white hover:bg-primary-700">+ Add row</button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              {editable.map((f) => <th key={f.name} className="px-2 py-1">{f.label}</th>)}
              <th className="px-2 py-1">Line Total</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id ?? `new-${i}`} className="border-t border-neutral-100">
                {editable.map((f) => (
                  <td key={f.name} className="px-1 py-1">
                    <input value={r[f.name] == null ? '' : String(r[f.name])}
                      onChange={(e) => set(i, f.name, e.target.value)}
                      className="h-8 w-full min-w-[6rem] rounded border border-neutral-300 px-2" />
                  </td>
                ))}
                <td className="px-2 py-1 tabular-nums">{(dec(r.qty) * dec(r.unit_price)).toFixed(2)}</td>
                <td className="px-1 py-1">
                  <button type="button" onClick={() => delRow(i)}
                    className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50">Delete</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={editable.length + 2} className="px-3 py-3 text-center text-xs text-neutral-400">No line items</td></tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t border-neutral-200 font-medium">
              <td colSpan={editable.length} className="px-2 py-2 text-right">Subtotal (preview)</td>
              <td className="px-2 py-2 tabular-nums">{subtotal.toFixed(2)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="px-3 py-1 text-[11px] text-neutral-400">Server recomputes line totals, subtotal, tax and total on save.</p>
    </div>
  )
}
