import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { EntitySchema, FieldSpec, RefHit } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { ReferencePicker } from './ReferencePicker'
import { ReferenceListPicker, type RefListItem } from './ReferenceListPicker'
import { LineItemsEditor, type LineRow } from './LineItemsEditor'
import { ApprovalStatePanel } from './ApprovalStatePanel'

interface Props { schema: EntitySchema; record: Record<string, unknown>; onClose: () => void }

export function RecordEditForm({ schema, record, onClose }: Props) {
  const id = String(record.id)
  const qc = useQueryClient()
  const { data: full } = useQuery({
    queryKey: ['admin-record', schema.system, schema.key, id],
    queryFn: () => adminApi.get(schema.system, schema.key, id),
  })
  const [form, setForm] = useState<Record<string, string>>({})
  const [refLabels, setRefLabels] = useState<Record<string, string>>({})
  const [refDirty, setRefDirty] = useState<Record<string, string>>({})   // name → new id
  // reference_list fields (invoice_ids / gr_ids): held as {id,label} so the
  // list stays readable, and sent back as a bare id array on save.
  const [refLists, setRefLists] = useState<Record<string, RefListItem[]>>({})
  const [refListDirty, setRefListDirty] = useState<Record<string, boolean>>({})
  const [lines, setLines] = useState<LineRow[]>([])
  const [regenPo, setRegenPo] = useState(true)
  const [vendorChanged, setVendorChanged] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!full) return
    setForm(Object.fromEntries(schema.fields.map((f) => [f.name, full[f.name] == null ? '' : String(full[f.name])])))
    setRefLabels(Object.fromEntries(schema.fields.filter((f) => f.type === 'reference')
      .map((f) => [f.name, f.ref_name_field ? String(full[f.ref_name_field] ?? '') : String(full[f.name] ?? '')])))
    setLines((full.line_items as LineRow[] | undefined) ?? [])
    // Labels are resolved server-side (service._ref_list_labels) — an id that
    // resolves to nothing still comes back, labelled, so a Save never silently
    // drops a link other services are still reading.
    const labels = (full._ref_list_labels ?? {}) as Record<string, RefListItem[]>
    setRefLists(Object.fromEntries(schema.fields.filter((f) => f.type === 'reference_list')
      .map((f) => [f.name, labels[f.name] ?? []])))
    setRefListDirty({})
  }, [full, schema])

  const isRef = (f: FieldSpec) => f.type === 'reference'
  const pickRef = (f: FieldSpec, hit: RefHit) => {
    setRefDirty((p) => ({ ...p, [f.name]: hit.id }))
    setRefLabels((p) => ({ ...p, [f.name]: hit.label }))
    if (f.name === 'vendor_id') setVendorChanged(true)
  }

  const save = async () => {
    setError('')
    if (schema.key === 'po' && vendorChanged && regenPo &&
        !window.confirm('Regenerate the PO number for the new vendor? This rewrites the number across PR/PA/GR/invoice/tasks/approval records.'))
      return
    const editable = schema.fields.filter((f) => f.editable)
    const patch: Record<string, unknown> = {}
    for (const f of editable) {
      if (isRef(f)) { if (refDirty[f.name] !== undefined) patch[f.name] = refDirty[f.name] }
      else if (f.type === 'reference_list') {
        // Only send it when actually touched: an untouched field would round-trip
        // a paid PA's links through validation for no reason.
        if (refListDirty[f.name]) patch[f.name] = (refLists[f.name] ?? []).map((v) => v.id)
      }
      else if (f.type === 'bool') patch[f.name] = form[f.name] === 'true'
      else patch[f.name] = form[f.name] === '' ? null : form[f.name]
    }
    if (schema.child) patch.line_items = lines
    setSaving(true)
    try {
      await adminApi.editWithOptions(schema.system, schema.key, id, patch,
        { regeneratePoNumber: schema.key === 'po' && vendorChanged && regenPo })
      // editWithOptions bypasses useAdminEdit's mutation, so mirror its invalidation
      // key here (see useAdminEdit in hooks/useAdmin.ts) plus the full-record query.
      await qc.invalidateQueries({ queryKey: ['admin', schema.system, schema.key] })
      await qc.invalidateQueries({ queryKey: ['admin-record', schema.system, schema.key, id] })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 className="mb-4 text-base font-semibold">Edit {schema.label} · {String(record[schema.number_field] ?? '')}</h3>

        <section className="mb-5 grid grid-cols-2 gap-3">
          {schema.fields.map((f) => (
            <div key={f.name}
              className={`flex flex-col gap-1${f.type === 'reference_list' ? ' col-span-2' : ''}`}>
              <label className="text-xs font-medium text-neutral-600">{f.label}{!f.editable && ' (read-only)'}</label>
              {isRef(f) && f.editable ? (
                <ReferencePicker system={schema.system} source={f.ref_source ?? ''}
                  valueLabel={refLabels[f.name] ?? ''} onPick={(h) => pickRef(f, h)} />
              ) : f.type === 'reference_list' ? (
                <ReferenceListPicker system={schema.system} source={f.ref_source ?? ''}
                  value={refLists[f.name] ?? []}
                  onChange={(next) => {
                    setRefLists((p) => ({ ...p, [f.name]: next }))
                    setRefListDirty((p) => ({ ...p, [f.name]: true }))
                  }} />
              ) : f.type === 'enum' && f.options ? (
                <select value={form[f.name] ?? ''} disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100">
                  {!f.options.includes(form[f.name]) && form[f.name] && <option value={form[f.name]}>{form[f.name]} (current)</option>}
                  {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : f.type === 'bool' ? (
                <input type="checkbox" checked={form[f.name] === 'true'} disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: String(e.target.checked) }))}
                  className="h-4 w-4 self-start" />
              ) : (
                <input value={form[f.name] ?? ''} disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100" />
              )}
            </div>
          ))}
        </section>

        {schema.key === 'po' && vendorChanged && (
          <label className="mb-4 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={regenPo} onChange={(e) => setRegenPo(e.target.checked)} />
            Regenerate PO number for the new vendor (cascades across linked records)
          </label>
        )}

        {schema.child && (
          <section className="mb-5">
            <LineItemsEditor child={schema.child} rows={lines} onChange={setLines} />
          </section>
        )}

        {/* Keep in sync with service.APPROVAL_STATE_ENTITIES on the backend. */}
        {['pr', 'po', 'pa', 'agreement'].includes(schema.key) && (
          <section className="mb-5">
            <ApprovalStatePanel system={schema.system} entity={schema.key} recordId={id}
              currentStep={full?.approval_step_idx == null ? null : Number(full.approval_step_idx)} />
          </section>
        )}

        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={save} disabled={saving}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
