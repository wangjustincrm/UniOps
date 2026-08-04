import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Check, Loader2, ToggleLeft, ToggleRight, ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

// ── Types ─────────────────────────────────────────────────────────────────────

interface CfmField {
  name: string
  label: string
  field_type: 'text' | 'number' | 'date' | 'select' | 'textarea'
  required: boolean
  options: string[]
  placeholder: string | null
}

interface CustomForm {
  id?: string
  code: string
  name: string
  fields: CfmField[]
  is_active: boolean
}

const FIELD_TYPES = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'select', label: 'Select (dropdown)' },
  { value: 'textarea', label: 'Textarea' },
]

function emptyField(): CfmField {
  return { name: '', label: '', field_type: 'text', required: false, options: [], placeholder: null }
}

function emptyForm(): CustomForm {
  return { code: '', name: '', fields: [], is_active: true }
}

// ── Field editor ──────────────────────────────────────────────────────────────

function FieldEditor({ field, onChange, onRemove }: {
  field: CfmField
  onChange: (f: CfmField) => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50">
      <button type="button" onClick={() => setOpen(v => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-neutral-600">
        <span className="flex items-center gap-1.5">
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          {field.label || 'New field'}
        </span>
        <button type="button" onClick={e => { e.stopPropagation(); onRemove() }}
          className="text-neutral-300 hover:text-danger-500 transition-colors">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </button>

      {open && (
        <div className="px-3 pb-3 grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] font-medium text-neutral-500">Label</label>
            <input value={field.label}
              onChange={e => onChange({ ...field, label: e.target.value,
                name: e.target.value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '') })}
              className="mt-0.5 w-full rounded border border-neutral-200 px-2 py-1 text-xs focus:outline-none" />
          </div>
          <div>
            <label className="text-[10px] font-medium text-neutral-500">Field Name (auto)</label>
            <input value={field.name} readOnly
              className="mt-0.5 w-full rounded border border-neutral-100 bg-neutral-100 px-2 py-1 text-xs text-neutral-400" />
          </div>
          <div>
            <label className="text-[10px] font-medium text-neutral-500">Type</label>
            <select value={field.field_type}
              onChange={e => onChange({ ...field, field_type: e.target.value as CfmField['field_type'] })}
              className="mt-0.5 w-full rounded border border-neutral-200 bg-white px-2 py-1 text-xs focus:outline-none">
              {FIELD_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          <div className="flex items-end gap-2">
            <label className="text-[10px] font-medium text-neutral-500 mb-1">Required</label>
            <button type="button" onClick={() => onChange({ ...field, required: !field.required })}
              className={cn('mb-1', field.required ? 'text-primary-600' : 'text-neutral-300')}>
              {field.required ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
            </button>
          </div>
          {field.field_type === 'select' && (
            <div className="col-span-2">
              <label className="text-[10px] font-medium text-neutral-500">Options (one per line)</label>
              <textarea rows={3}
                value={field.options.join('\n')}
                onChange={e => onChange({ ...field, options: e.target.value.split('\n').filter(Boolean) })}
                className="mt-0.5 w-full rounded border border-neutral-200 px-2 py-1 text-xs focus:outline-none resize-none" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Form editor ───────────────────────────────────────────────────────────────

function FormEditor({ form, onChange, onRemove }: {
  form: CustomForm
  onChange: (f: CustomForm) => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(true)

  const updateField = (i: number, f: CfmField) =>
    onChange({ ...form, fields: form.fields.map((fi, idx) => idx === i ? f : fi) })

  const removeField = (i: number) =>
    onChange({ ...form, fields: form.fields.filter((_, idx) => idx !== i) })

  return (
    <div className="rounded-xl border border-neutral-200 bg-white">
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-100">
        <button type="button" onClick={() => setOpen(v => !v)}
          className="flex items-center gap-2 text-sm font-semibold text-neutral-800">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {form.name || 'New Form'}
          <span className="text-xs font-mono text-neutral-400">{form.code ? `CFM_${form.code}` : ''}</span>
        </button>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => onChange({ ...form, is_active: !form.is_active })}
            className={cn('flex items-center gap-1 text-xs font-medium',
              form.is_active ? 'text-success-600' : 'text-neutral-400')}>
            {form.is_active ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
            {form.is_active ? 'Active' : 'Inactive'}
          </button>
          <button type="button" onClick={onRemove}
            className="text-neutral-300 hover:text-danger-500 transition-colors">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {open && (
        <div className="p-4 flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600">Form Name</label>
              <input value={form.name} onChange={e => onChange({ ...form, name: e.target.value })}
                placeholder="e.g. Conference & Training"
                className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600">
                Code <span className="text-neutral-400 font-normal">(UPPERCASE, no spaces)</span>
              </label>
              <input value={form.code}
                onChange={e => onChange({ ...form, code: e.target.value.toUpperCase().replace(/\s+/g, '_') })}
                placeholder="e.g. CONF"
                className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm font-mono focus:outline-none focus:border-primary-400" />
              {form.code && (
                <p className="mt-1 text-[11px] text-neutral-400">
                  Action key: <span className="font-mono">cfm_{form.code.toLowerCase()}</span>
                </p>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <p className="text-xs font-medium text-neutral-500">Fields</p>
            {form.fields.map((f, i) => (
              <FieldEditor key={i} field={f}
                onChange={nf => updateField(i, nf)}
                onRemove={() => removeField(i)} />
            ))}
            <button type="button"
              onClick={() => onChange({ ...form, fields: [...form.fields, emptyField()] })}
              className="flex items-center gap-1.5 self-start text-xs font-medium text-primary-700 hover:text-primary-900 mt-1">
              <Plus className="h-3.5 w-3.5" /> Add field
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CfmAdminPage() {
  const qc = useQueryClient()
  const { data: loaded, isLoading } = useQuery<CustomForm[]>({
    queryKey: ['custom-forms-admin'],
    queryFn: () => api.get<CustomForm[]>('/api/v1/expenses/custom-forms'),
  })

  const [forms, setForms] = useState<CustomForm[]>([])
  const [originalCodes, setOriginalCodes] = useState<Set<string>>(new Set())
  const [initialised, setInitialised] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  if (loaded && !initialised) {
    setForms(loaded)
    setOriginalCodes(new Set(loaded.map((f) => f.code)))
    setInitialised(true)
  }

  // Sync local edits to the dedicated custom-forms table: upsert each form,
  // and deactivate (not delete — CFM-005) any form removed from the list.
  const mutation = useMutation({
    mutationFn: async () => {
      const currentCodes = new Set(forms.map((f) => f.code))
      for (const f of forms) {
        if (!f.code || !f.name) continue
        const body = { name: f.name, fields: f.fields, is_active: f.is_active }
        if (originalCodes.has(f.code)) {
          await api.patch(`/api/v1/expenses/custom-forms/${f.code}`, body)
        } else {
          await api.post('/api/v1/expenses/custom-forms', { code: f.code, ...body })
        }
      }
      for (const code of originalCodes) {
        if (!currentCodes.has(code)) {
          await api.patch(`/api/v1/expenses/custom-forms/${code}`, { is_active: false })
        }
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['custom-forms-admin'] })
      qc.invalidateQueries({ queryKey: ['custom-forms-active'] })
      setOriginalCodes(new Set(forms.map((f) => f.code)))
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
    onError: (e: any) => setError(e.message || 'Save failed'),
  })

  if (isLoading) return (
    <div className="flex items-center justify-center py-16">
      <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
    </div>
  )

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-neutral-900">Custom Forms (CFM)</h1>
        <p className="mt-0.5 text-sm text-neutral-500">
          Define custom expense form types. Each form gets its own action key for approval workflow binding.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {forms.length === 0 && (
          <div className="rounded-xl border border-dashed border-neutral-200 py-10 text-center text-sm text-neutral-400">
            No custom forms. Click "Add Form" to create one.
          </div>
        )}
        {forms.map((form, i) => (
          <FormEditor key={i} form={form}
            onChange={nf => setForms(prev => prev.map((f, idx) => idx === i ? nf : f))}
            onRemove={() => setForms(prev => prev.filter((_, idx) => idx !== i))} />
        ))}
        <button type="button" onClick={() => setForms(prev => [...prev, emptyForm()])}
          className="flex items-center gap-2 self-start rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
          <Plus className="h-4 w-4" /> Add Form
        </button>
      </div>

      {error && <ErrorBanner message={error} />}

      <div className="flex items-center gap-3 border-t border-neutral-100 pt-4">
        <button onClick={() => { setError(''); mutation.mutate() }}
          disabled={mutation.isPending}
          className="flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors">
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Save Forms
        </button>
        {saved && <span className="flex items-center gap-1.5 text-sm text-success-600"><Check className="h-4 w-4" /> Saved</span>}
      </div>
    </div>
  )
}
