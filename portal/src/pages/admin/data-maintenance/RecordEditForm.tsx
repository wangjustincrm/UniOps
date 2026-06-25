import { useState } from 'react'
import type { EntitySchema } from '@/services/adminApi'
import { useAdminEdit } from '@/hooks/useAdmin'

interface Props {
  schema: EntitySchema
  record: Record<string, unknown>
  onClose: () => void
}

export function RecordEditForm({ schema, record, onClose }: Props) {
  const [form, setForm] = useState<Record<string, string>>(
    () => Object.fromEntries(schema.fields.map((f) => [f.name, record[f.name] == null ? '' : String(record[f.name])]))
  )
  const [error, setError] = useState('')
  const edit = useAdminEdit(schema.system, schema.key)

  const editable = schema.fields.filter((f) => f.editable)

  const save = async () => {
    setError('')
    const patch = Object.fromEntries(editable.map((f) => [f.name, form[f.name] === '' ? null : form[f.name]]))
    try {
      await edit.mutateAsync({ id: String(record.id), patch })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 className="mb-4 text-base font-semibold">Edit {schema.label} · {String(record[schema.number_field] ?? '')}</h3>
        <div className="flex flex-col gap-3">
          {schema.fields.map((f) => (
            <div key={f.name} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">{f.label}{!f.editable && ' (read-only)'}</label>
              {f.type === 'enum' && f.options ? (
                <select
                  value={form[f.name]}
                  disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100 disabled:text-neutral-500"
                >
                  {/* keep the current value selectable even if it's not (yet) a canonical option */}
                  {!f.options.includes(form[f.name]) && form[f.name] !== '' && (
                    <option value={form[f.name]}>{form[f.name]} (current)</option>
                  )}
                  {f.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                </select>
              ) : (
                <input
                  value={form[f.name]}
                  disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100 disabled:text-neutral-500"
                />
              )}
            </div>
          ))}
        </div>
        {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={save} disabled={edit.isPending}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50">
            {edit.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
