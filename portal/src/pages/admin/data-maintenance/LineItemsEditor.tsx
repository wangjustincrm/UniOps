import { useMemo } from 'react'
import type { ChildSchema } from '@/services/adminApi'

import { Button, Input, Select, TBody, TFoot, THead, Table, Td, Th, Tr } from '@uniops/shell'
export interface LineRow { id?: string; [k: string]: unknown }

interface Props {
  child: ChildSchema
  rows: LineRow[]
  onChange: (rows: LineRow[]) => void
}

const dec = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }

export function LineItemsEditor({ child, rows, onChange }: Props) {
  const editable = child.fields.filter((f) => f.editable)
  const qtyField = child.qty_field ?? 'qty'
  const priceField = child.price_field ?? 'unit_price'
  const allowAdd = child.allow_add !== false
  const lineTotal = (r: LineRow) => dec(r[qtyField]) * dec(r[priceField])

  const set = (i: number, name: string, value: string) => {
    const next = rows.map((r, j) => (j === i ? { ...r, [name]: value } : r))
    onChange(next)
  }
  const addRow = () => onChange([...rows, Object.fromEntries(editable.map((f) => [f.name, ''])) as LineRow])
  const delRow = (i: number) => onChange(rows.filter((_, j) => j !== i))

  const subtotal = useMemo(
    () => rows.reduce((s, r) => s + lineTotal(r), 0), [rows, qtyField, priceField])

  return (
    <div className="rounded-lg border border-line">
      <div className="flex items-center justify-between border-b border-line-subtle px-3 py-2">
        <span className="text-sm font-medium">{child.table_label}</span>
        {allowAdd && (
          <Button size="xs" type="button" onClick={addRow}
           >+ Add row</Button>
        )}
      </div>
      <div className="overflow-x-auto">
        <Table density="compact">
          <THead>
            <Tr className="text-left text-xs text-fg-muted">
              {editable.map((f) => <Th key={f.name}>{f.label}</Th>)}
              <Th>Line Total</Th>
              <Th />
            </Tr>
          </THead>
          <TBody>
            {rows.map((r, i) => (
              <Tr key={r.id ?? `new-${i}`}>
                {editable.map((f) => (
                  <Td key={f.name}>
                    {f.type === 'enum' && f.options ? (
                      <Select selectSize="sm" value={r[f.name] == null ? '' : String(r[f.name])}
                        onChange={(e) => set(i, f.name, e.target.value)}
                        className="w-full min-w-[6rem]">
                        {r[f.name] == null && <option value="" />}
                        {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                      </Select>
                    ) : (
                      <Input inputSize="sm" value={r[f.name] == null ? '' : String(r[f.name])}
                        onChange={(e) => set(i, f.name, e.target.value)}
                        className="w-full min-w-[6rem]" />
                    )}
                  </Td>
                ))}
                <Td className="tabular-nums">{lineTotal(r).toFixed(2)}</Td>
                <Td>
                  <Button type="button" variant="danger-ghost" size="xs" onClick={() => delRow(i)}>Delete</Button>
                </Td>
              </Tr>
            ))}
            {rows.length === 0 && (
              <Tr><Td align="center" colSpan={editable.length + 2} className="text-xs text-fg-muted">No line items</Td></Tr>
            )}
          </TBody>
          <TFoot>
            <Tr className="font-medium">
              <Td align="right" colSpan={editable.length}>Subtotal (preview)</Td>
              <Td className="tabular-nums">{subtotal.toFixed(2)}</Td>
              <Td />
            </Tr>
          </TFoot>
        </Table>
      </div>
      <p className="px-3 py-1 text-2xs text-fg-muted">{child.note ?? 'Server recomputes line totals, subtotal, tax and total on save.'}</p>
    </div>
  )
}
