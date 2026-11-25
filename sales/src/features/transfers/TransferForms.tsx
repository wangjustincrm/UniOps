import { z } from 'zod'
import { Autocomplete, MasterDetailForm, SchemaForm, numberColumn, problemDone, type EditableColumn, type FieldSpec } from '@/platform-ui'
import { itemOptions, warehouseOptions } from '../configuration/lookups'
import { amendTransfer, createTransfer, resolveTransfer, updateTransfer, type TransferDetail } from './transfersApi'
import { errorMessages } from './transferRules'

type Line = { item_code: string; qty_ea: string }
const editColumns: EditableColumn<Line>[] = [
  { id: 'item', header: 'Item', cell: ({ row, index, update, invalid }) => (
    <Autocomplete aria-label={`Item ${index + 1}`} aria-invalid={invalid || undefined} value={row.item_code || null}
      onChange={(v) => update({ item_code: v ?? '' })} loadOptions={itemOptions} />
  ) },
  numberColumn<Line>('qty', 'EA', { label: (_, i) => `EA ${i + 1}`, get: (l) => l.qty_ea, set: (v) => ({ qty_ea: v }), min: 1 }),
]
const lineProblem = (l: Line) => (!l.item_code ? 'choose an item' : Number.isInteger(Number(l.qty_ea)) && Number(l.qty_ea) > 0 ? null : 'at least 1 EA')

const headerSchema = z.object({
  from_warehouse_id: z.string().min(1, 'Choose the warehouse it leaves'),
  to_warehouse_id: z.string().min(1, 'Choose the warehouse it goes to'),
  notes: z.string().trim().max(2000).nullable().optional(),
}).refine((v) => v.from_warehouse_id !== v.to_warehouse_id, { message: 'The two warehouses must differ', path: ['to_warehouse_id'] })
const headerFields: FieldSpec[] = [
  { name: 'from_warehouse_id', label: 'From warehouse', type: 'reference', required: true, loadOptions: warehouseOptions },
  { name: 'to_warehouse_id', label: 'To warehouse', type: 'reference', required: true, loadOptions: warehouseOptions },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

/** A Draft transfer, new or edited (FR-OB-80): the warehouses and lines in EA. */
export function TransferForm({ t, onSaved, onCancel }: { t?: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<Line>
      schema={headerSchema}
      fields={headerFields}
      defaultValues={{ from_warehouse_id: t ? String(t.from_warehouse_id) : '', to_warehouse_id: t ? String(t.to_warehouse_id) : '',
        notes: t?.notes ?? '' }}
      lines={{ label: 'Transfer lines', columns: editColumns, problem: lineProblem, add: () => ({ item_code: '', qty_ea: '' }),
        removable: true, initial: t ? t.lines.map((l) => ({ item_code: l.item_code, qty_ea: String(l.qty_ea) })) : [{ item_code: '', qty_ea: '' }] }}
      submitLabel={t ? 'Save changes' : 'Save draft'}
      errorMessages={errorMessages}
      onCancel={onCancel}
      version={t?.version}
      onSubmit={async (v, lines, edit) => {
        const body = { from_warehouse_id: Number(v.from_warehouse_id), to_warehouse_id: Number(v.to_warehouse_id),
          notes: (v.notes as string) ?? null, lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) }
        onSaved(t ? await updateTransfer(t.id, edit.version ?? t.version, body) : await createTransfer(body))
      }}
    />
  )
}

const amendSchema = z.object({
  notes: z.string().trim().max(2000).nullable().optional(),
  reason: z.string().trim().min(3, 'Say what was wrong (at least 3 characters)').max(2000),
})
const amendFields: FieldSpec[] = [
  { name: 'reason', label: 'What was wrong', type: 'textarea', required: true,
    hint: 'Kept with the transfer. The 943 that went is Replaced; a new one (a new control number) goes on the next pass.' },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

/** A sent transfer corrected and sent again (user 2026-11-13), until a 944 answers it: same warehouses, new lines, why. */
export function AmendForm({ t, onSaved, onCancel }: { t: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<Line>
      schema={amendSchema}
      fields={amendFields}
      defaultValues={{ notes: t.notes ?? '', reason: '' }}
      lines={{ label: 'Transfer lines', columns: editColumns, problem: lineProblem, add: () => ({ item_code: '', qty_ea: '' }),
        removable: true, initial: t.lines.map((l) => ({ item_code: l.item_code, qty_ea: String(l.qty_ea) })) }}
      submitLabel="Amend and send again"
      errorMessages={errorMessages}
      onCancel={onCancel}
      version={t.version}
      onSubmit={async (v, lines, edit) => {
        const out = await amendTransfer(t.id, edit.version ?? t.version, { notes: (v.notes as string) || null, reason: String(v.reason),
          lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) })
        onSaved(out)
        return out
      }}
      done={(r) => problemDone((r as TransferDetail | undefined)?.problem)}
    />
  )
}

const resolveSchema = z.object({
  how: z.enum(['corrected', 'accepted'], { message: 'Choose how it is settled' }),
  reason: z.string().trim().min(3, 'Say why (at least 3 characters)').max(2000),
})
const resolveFields: FieldSpec[] = [
  { name: 'how', label: 'How', type: 'segmented', required: true, options: [
    { value: 'corrected', label: 'Our 943 was wrong: correct it to what arrived' },
    { value: 'accepted', label: 'The difference is real: accept it' }],
    hint: 'Corrected: the transfer\'s lines become the 944\'s items and quantities, nothing stays in transit. Accepted: the lines stay as sent; what did not arrive is written off in transit (or what came extra is added).' },
  { name: 'reason', label: 'Why', type: 'textarea', required: true },
]

/** A receipt difference settled by a person (user 2026-11-13): the transfer is Received, its tasks closed. */
export function ResolveForm({ t, onSaved, onCancel }: { t: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <SchemaForm schema={resolveSchema} fields={resolveFields} submitLabel="Settle the difference" onCancel={onCancel} errorMessages={errorMessages}
      defaultValues={{ how: '', reason: '' }}
      onSubmit={async (v) => {
        const out = await resolveTransfer(t.id, t.version, v.how as 'corrected' | 'accepted', String(v.reason))
        onSaved(out)
        return out
      }} />
  )
}
