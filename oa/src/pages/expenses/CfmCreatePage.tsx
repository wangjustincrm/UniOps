import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

// ── Types ─────────────────────────────────────────────────────────────────────

interface CfmField {
  name: string; label: string
  field_type: 'text' | 'number' | 'date' | 'select' | 'textarea'
  required: boolean; options: string[]; placeholder: string | null
}
interface CustomForm { code: string; name: string; fields: CfmField[]; is_active: boolean }

// ── Dynamic field renderer ────────────────────────────────────────────────────

function DynamicField({
  field, value, onChange,
}: {
  field: CfmField; value: string; onChange: (v: string) => void
}) {
  const base = 'w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400'

  if (field.field_type === 'textarea') {
    return (
      <textarea value={value} onChange={e => onChange(e.target.value)}
        placeholder={field.placeholder ?? ''}
        required={field.required} rows={3}
        className={`${base} resize-none`} />
    )
  }
  if (field.field_type === 'select') {
    return (
      <select value={value} onChange={e => onChange(e.target.value)}
        required={field.required}
        className={`${base} bg-white`}>
        <option value="">— Select —</option>
        {field.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
      </select>
    )
  }
  return (
    <input
      type={field.field_type === 'number' ? 'number' : field.field_type === 'date' ? 'date' : 'text'}
      value={value} onChange={e => onChange(e.target.value)}
      placeholder={field.placeholder ?? ''}
      required={field.required}
      className={base}
    />
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CfmCreatePage() {
  const { formCode } = useParams<{ formCode: string }>()
  const replaceTab = useReplaceTab(oaRoutes)

  const { data: forms, isLoading } = useQuery<CustomForm[]>({
    queryKey: ['custom-forms-active'],
    queryFn: () => api.get<CustomForm[]>('/api/v1/expenses/custom-forms?active_only=true'),
  })

  const form = forms?.find(
    f => f.code.toLowerCase() === formCode?.toLowerCase() && f.is_active
  )

  const [values, setValues] = useState<Record<string, string>>({})
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const setValue = (name: string, v: string) => setValues(p => ({ ...p, [name]: v }))

  const mutation = useMutation({
    mutationFn: (body: object) => api.post('/api/v1/expenses', body),
    onSuccess: (data: any) => replaceTab(`/expenses/${data.id}`),
    onError: (e: any) => setError(e.message || 'Failed to create claim'),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form) return
    setError('')

    // Encode custom field answers into notes JSON
    const fieldData = Object.fromEntries(
      (form.fields ?? []).map(f => [f.name, values[f.name] ?? ''])
    )

    mutation.mutate({
      claim_type: `CFM_${form.code}`,
      submission_date: new Date().toISOString().slice(0, 10),
      notes: JSON.stringify({ ...fieldData, _user_notes: notes }),
      line_items: [],
    })
  }

  if (isLoading) return (
    <div className="flex items-center justify-center py-16">
      <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
    </div>
  )

  if (!form) return (
    <div className="py-16 text-center text-sm text-danger-500">
      Custom form "{formCode}" not found or is inactive.
    </div>
  )

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-lg">
      <div>
        <a href="/expenses" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to Expenses
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">{form.name}</h1>
        <p className="mt-0.5 text-sm text-neutral-500 font-mono">CFM_{form.code}</p>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-4">
        {(form.fields ?? []).map(field => (
          <div key={field.name}>
            <label className="mb-1 block text-xs font-medium text-neutral-600">
              {field.label}
              {field.required && <span className="ml-1 text-danger-400">*</span>}
            </label>
            <DynamicField
              field={field}
              value={values[field.name] ?? ''}
              onChange={v => setValue(field.name, v)}
            />
          </div>
        ))}

        {form.fields.length === 0 && (
          <p className="text-sm text-neutral-400">This form has no custom fields. Add fields in Admin → Custom Forms.</p>
        )}

        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Additional Notes</label>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
            className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none" />
        </div>
      </div>

      {error && <ErrorBanner message={error} />}

      <button type="submit" disabled={mutation.isPending}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-6 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors self-start">
        {mutation.isPending ? 'Saving…' : 'Save Draft'}
      </button>
    </form>
  )
}
