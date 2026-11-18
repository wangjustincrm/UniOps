import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Check, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

import { Button, DisclosureButton, Input, Select, Switch, Textarea, Card } from '@uniops/shell'
// ── Types ─────────────────────────────────────────────────────────────────────

interface CfmField {
  name: string
  label: string
  field_type: 'text' | 'number' | 'date' | 'select' | 'textarea'
  required: boolean
  options: string[]
  placeholder: string | null
  // Client-only: the Options box as typed. Splitting it on every keystroke
  // dropped the empty line Enter had just made, so options ran together
  // (ISS-0198); it becomes `options` when the forms are saved.
  options_text?: string
}

interface CustomForm {
  id?: string
  code: string
  name: string
  // Shown to employees on the form (OA.md §6.1).
  description?: string | null
  default_currency: string
  // Read-only: the chain the form actually uses — cfm_<code> if configured
  // in Portal → Approval Workflows, else cfm. Absent until the form is saved.
  effective_workflow_key?: string | null
  fields: CfmField[]
  is_active: boolean
}

const CURRENCIES = ['CAD', 'USD']

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

// One option per non-blank line of the Options box, trimmed.
function toSavedField({ options_text, ...f }: CfmField): CfmField {
  if (options_text === undefined) return f
  return { ...f, options: options_text.split('\n').map((o) => o.trim()).filter(Boolean) }
}

function emptyForm(): CustomForm {
  return { code: '', name: '', description: '', default_currency: 'CAD', fields: [], is_active: true }
}

// ── Field editor ──────────────────────────────────────────────────────────────

function FieldEditor({ field, onChange, onRemove }: {
  field: CfmField
  onChange: (f: CfmField) => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="rounded-lg border border-line bg-surface-subtle">
      {/* Header row: toggle and remove are siblings (a button inside a button is invalid HTML). */}
      <div className="flex items-center pr-1">
        <DisclosureButton open={open} iconSize="sm" onClick={() => setOpen(v => !v)}
          className="flex-1 gap-1.5 px-3 py-2 text-xs font-semibold text-fg-secondary">
          {field.label || 'New field'}
        </DisclosureButton>
        <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" type="button" onClick={onRemove}>
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {open && (
        <div className="px-3 pb-3 grid grid-cols-2 gap-2">
          <div>
            <label className="text-2xs font-medium text-fg-muted">Label</label>
            <Input inputSize="sm" value={field.label}
              onChange={e => onChange({ ...field, label: e.target.value,
                name: e.target.value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '') })}
              className="mt-0.5 w-full" />
          </div>
          <div>
            <label className="text-2xs font-medium text-fg-muted">Field Name (auto)</label>
            <Input inputSize="sm" value={field.name} readOnly
              className="mt-0.5 w-full" />
          </div>
          <div>
            <label className="text-2xs font-medium text-fg-muted">Type</label>
            <Select selectSize="sm" value={field.field_type}
              onChange={e => onChange({ ...field, field_type: e.target.value as CfmField['field_type'] })}
              className="mt-0.5 w-full">
              {FIELD_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
          </div>
          <div className="flex items-end gap-2">
            <label className="text-2xs font-medium text-fg-muted mb-1">Required</label>
            <Switch size="sm" className="mb-1" aria-label="Required" checked={field.required}
              onCheckedChange={(required) => onChange({ ...field, required })} />
          </div>
          {field.field_type === 'select' && (
            <div className="col-span-2">
              <label className="text-2xs font-medium text-fg-muted">Options (one per line)</label>
              <Textarea rows={3}
                value={field.options_text ?? field.options.join('\n')}
                onChange={e => onChange({ ...field, options_text: e.target.value })}
                className="mt-0.5 w-full resize-none" />
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
    <Card>
      <div className="flex items-center justify-between px-4 py-3 border-b border-line-subtle">
        <DisclosureButton open={open} onClick={() => setOpen(v => !v)}
          className="text-sm font-semibold text-fg">
          {form.name || 'New Form'}
          <span className="text-xs font-mono text-fg-muted">{form.code ? `CFM_${form.code}` : ''}</span>
        </DisclosureButton>
        <div className="flex items-center gap-2">
          <label className={cn('flex items-center gap-2 text-xs font-medium', form.is_active ? 'text-success-fg' : 'text-fg-muted')}>
            <Switch size="sm" checked={form.is_active} onCheckedChange={(is_active) => onChange({ ...form, is_active })} />
            {form.is_active ? 'Active' : 'Inactive'}
          </label>
          <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" type="button" onClick={onRemove}
           >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {open && (
        <div className="p-4 flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Form Name</label>
              <Input value={form.name} onChange={e => onChange({ ...form, name: e.target.value })}
                placeholder="e.g. Conference & Training"
                className="w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">
                Code <span className="text-fg-muted font-normal">(UPPERCASE, no spaces)</span>
              </label>
              <Input value={form.code}
                onChange={e => onChange({ ...form, code: e.target.value.toUpperCase().replace(/\s+/g, '_') })}
                placeholder="e.g. CONF"
                className="w-full font-mono" />
              {form.code && (
                <p className="mt-1 text-2xs text-fg-muted">
                  Action key: <span className="font-mono">cfm_{form.code.toLowerCase()}</span>
                </p>
              )}
            </div>
            <div className="col-span-2">
              <label className="mb-1 block text-xs font-medium text-fg-secondary">
                Description <span className="text-fg-muted font-normal">(shown to employees)</span>
              </label>
              <Textarea rows={2} value={form.description ?? ''}
                onChange={e => onChange({ ...form, description: e.target.value })}
                className="w-full resize-none" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Default Currency</label>
              <Select value={form.default_currency}
                onChange={e => onChange({ ...form, default_currency: e.target.value })}
                className="w-full">
                {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Workflow</label>
              <p className="py-2 text-sm text-fg-secondary">
                {form.effective_workflow_key
                  ? <span className="font-mono">{form.effective_workflow_key}</span>
                  : <span className="text-fg-muted">Set when saved</span>}
              </p>
              <p className="text-2xs text-fg-muted">
                Uses <span className="font-mono">cfm_{form.code ? form.code.toLowerCase() : '<code>'}</span> if
                configured in Portal → Approval Workflows, otherwise the default <span className="font-mono">cfm</span>.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <p className="text-xs font-medium text-fg-muted">Fields</p>
            {form.fields.map((f, i) => (
              <FieldEditor key={i} field={f}
                onChange={nf => updateField(i, nf)}
                onRemove={() => removeField(i)} />
            ))}
            <Button variant="link" type="button"
              onClick={() => onChange({ ...form, fields: [...form.fields, emptyField()] })}
              className="self-start mt-1 text-xs">
              <Plus className="h-3.5 w-3.5" /> Add field
            </Button>
          </div>
        </div>
      )}
    </Card>
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
        const body = {
          name: f.name, description: f.description ?? '', default_currency: f.default_currency,
          fields: f.fields.map(toSavedField), is_active: f.is_active,
        }
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
      // Re-seed from the refetch so a new form shows its effective workflow.
      setInitialised(false)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
    onError: (e: any) => setError(e.message || 'Save failed'),
  })

  if (isLoading) return (
    <div className="flex items-center justify-center py-16">
      <Loader2 className="h-6 w-6 animate-spin text-fg-muted" />
    </div>
  )

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold text-fg">Custom Forms (CFM)</h1>
        <p className="mt-0.5 text-sm text-fg-muted">
          Define custom expense form types. Each form gets its own action key for approval workflow binding.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {forms.length === 0 && (
          <div className="rounded-xl border border-dashed border-line py-10 text-center text-sm text-fg-muted">
            No custom forms. Click "Add Form" to create one.
          </div>
        )}
        {forms.map((form, i) => (
          <FormEditor key={i} form={form}
            onChange={nf => setForms(prev => prev.map((f, idx) => idx === i ? nf : f))}
            onRemove={() => setForms(prev => prev.filter((_, idx) => idx !== i))} />
        ))}
        <Button variant="secondary" size="md" type="button" onClick={() => setForms(prev => [...prev, emptyForm()])}
          className="self-start">
          <Plus className="h-4 w-4" /> Add Form
        </Button>
      </div>

      {error && <ErrorBanner message={error} />}

      <div className="flex items-center gap-3 border-t border-line-subtle pt-4">
        <Button size="md" onClick={() => { setError(''); mutation.mutate() }}
          disabled={mutation.isPending}
         >
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Save Forms
        </Button>
        {saved && <span className="flex items-center gap-1.5 text-sm text-success-fg"><Check className="h-4 w-4" /> Saved</span>}
      </div>
    </div>
  )
}
