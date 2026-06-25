/**
 * Factor Library — reusable decomposition factor templates.
 *
 * Source of truth for templates that can be cloned into a Budget Account's
 * decomposition factors. Library lives in budget-api (`/factor-templates`);
 * EPMS catalog page reads from here when an admin clicks "From Library…" on
 * an account.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Layers, Plus, Pencil, Trash2, X, Check, Search,
  ChevronDown, ChevronRight, AlertCircle,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { budgetApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const MANAGE_ROLES = new Set(['system_admin', 'finance_manager', 'finance_bp'])

interface FactorTemplateValue {
  id: string
  template_id: string
  value_code: string
  value_name: string
  sort_order: number
  is_active: boolean
}

interface FactorTemplate {
  id: string
  factor_code: string
  factor_name: string
  description: string | null
  is_active: boolean
  values: FactorTemplateValue[]
}

interface TemplateFormData {
  factor_code: string
  factor_name: string
  description: string
}

const BLANK_TEMPLATE: TemplateFormData = { factor_code: '', factor_name: '', description: '' }

export default function FactorLibraryPage() {
  const { user } = useAuthStore()
  if (!user) return <Navigate to="/login" replace />
  if (!MANAGE_ROLES.has(user.role)) return <Navigate to="/" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/budget/factors"
      title="Factor Library"
      subtitle="Reusable decomposition factor templates. Attach to a Budget Account from the EPMS Account Catalog."
    >
      <div className="mx-auto max-w-5xl">
        <FactorLibrary />
      </div>
    </PortalChromeLayout>
  )
}

function FactorLibrary() {
  const qc = useQueryClient()
  const { data: templates = [], isLoading } = useQuery<FactorTemplate[]>({
    queryKey: ['factor-templates'],
    queryFn: () => budgetApi.get<FactorTemplate[]>('/factor-templates'),
  })
  const [search, setSearch] = useState('')
  const [showActiveOnly, setShowActiveOnly] = useState(false)
  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [error, setError] = useState('')

  const filtered = useMemo(() => {
    return templates.filter((t) => {
      if (showActiveOnly && !t.is_active) return false
      const q = search.toLowerCase()
      if (!q) return true
      return t.factor_code.toLowerCase().includes(q) || t.factor_name.toLowerCase().includes(q)
    })
  }, [templates, search, showActiveOnly])

  const editing = typeof mode === 'object' ? templates.find((t) => t.id === mode.edit) : null
  const usedCodes = (excludeId?: string) =>
    templates.filter((t) => t.id !== excludeId).map((t) => t.factor_code.toLowerCase())

  const createMut = useMutation({
    mutationFn: (body: TemplateFormData & { values?: never }) =>
      budgetApi.post<FactorTemplate>('/factor-templates', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => setError(e.message),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<TemplateFormData> & { is_active?: boolean } }) =>
      budgetApi.patch<FactorTemplate>(`/factor-templates/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => setError(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (id: string) => budgetApi.delete(`/factor-templates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => setError(e.message),
  })

  const toggleExpanded = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          <Layers className="h-4 w-4" />
        </div>
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-neutral-900">Factor Templates</h2>
          <p className="mt-0.5 text-xs text-neutral-500 leading-relaxed">
            Templates are reusable starting points. Selecting one from a Budget Account copies its values into that
            account&rsquo;s factors — later template edits do not propagate to previously attached accounts.
            Managed by System Admin, Finance Manager, and Finance BP.
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-3 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-4 w-4" /> {error}
          <button onClick={() => setError('')} className="ml-auto text-red-400 hover:text-red-600">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 flex-wrap">
          <div className="relative min-w-52">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <input
              placeholder="Search by code or name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-10 w-full rounded-lg border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]"
            />
          </div>
          <label className="flex items-center gap-1.5 text-xs text-neutral-600">
            <input
              type="checkbox"
              checked={showActiveOnly}
              onChange={(e) => setShowActiveOnly(e.target.checked)}
              className="h-4 w-4 rounded border-neutral-300"
            />
            Active only
          </label>
        </div>
        <button
          onClick={() => { setError(''); setMode('add') }}
          disabled={mode !== 'none'}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />Add Template
        </button>
      </div>

      {mode === 'add' && (
        <TemplateForm
          title="Add Factor Template"
          initial={BLANK_TEMPLATE}
          existingCodes={usedCodes()}
          codeEditable
          onSave={(d) => { createMut.mutate({ ...d, factor_code: d.factor_code.trim() }); setMode('none') }}
          onCancel={() => setMode('none')}
        />
      )}
      {editing && (
        <TemplateForm
          title={`Edit — ${editing.factor_name}`}
          initial={{
            factor_code: editing.factor_code,
            factor_name: editing.factor_name,
            description: editing.description ?? '',
          }}
          existingCodes={usedCodes(editing.id)}
          codeEditable={false}
          isActive={editing.is_active}
          onToggleActive={(v) => updateMut.mutate({ id: editing.id, body: { is_active: v } })}
          onSave={(d) => {
            updateMut.mutate({
              id: editing.id,
              body: { factor_name: d.factor_name, description: d.description },
            })
            setMode('none')
          }}
          onCancel={() => setMode('none')}
        />
      )}

      <div className="rounded-lg border border-neutral-200 overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading templates…</div>
        ) : !filtered.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">
            {templates.length === 0
              ? 'No factor templates yet — click "Add Template" to create your first one.'
              : 'No templates match the current filters.'}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                {['', 'Code', 'Name', 'Values', 'Status', ''].map((h, i) => (
                  <th
                    key={i}
                    className={cn(
                      'px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500',
                      i === 0 && 'w-8', i === 3 && 'w-20 text-center', i === 4 && 'w-24', i === 5 && 'w-24',
                    )}
                  >{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((t, i) => {
                const isOpen = expanded.has(t.id)
                const activeValues = t.values.filter((v) => v.is_active).length
                return (
                  <>
                    <tr key={t.id} className={cn('border-b border-neutral-100', i % 2 === 1 ? 'bg-neutral-50' : 'bg-white')}>
                      <td className="px-3 py-2.5">
                        <button onClick={() => toggleExpanded(t.id)} className="text-neutral-400 hover:text-neutral-600">
                          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2.5"><span className="inline-flex items-center rounded-md bg-neutral-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-neutral-700">{t.factor_code}</span></td>
                      <td className="px-3 py-2.5 font-medium text-neutral-800">
                        <div className="flex flex-col">
                          <span>{t.factor_name}</span>
                          {t.description && <span className="text-[11px] text-neutral-500">{t.description}</span>}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <span className={cn('inline-flex items-center justify-center rounded-full w-7 h-7 text-xs font-semibold', activeValues > 0 ? 'bg-primary-50 text-primary-700' : 'bg-neutral-100 text-neutral-400')}>
                          {activeValues}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium', t.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                          {t.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center justify-end gap-1">
                          {deleteConfirm === t.id ? (
                            <>
                              <button onClick={() => { deleteMut.mutate(t.id); setDeleteConfirm(null) }} className="flex h-7 w-7 items-center justify-center rounded text-red-600 hover:bg-red-50" title="Confirm delete"><Check className="h-3.5 w-3.5" /></button>
                              <button onClick={() => setDeleteConfirm(null)} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"><X className="h-3.5 w-3.5" /></button>
                            </>
                          ) : (
                            <>
                              <button onClick={() => { setError(''); setMode({ edit: t.id }); setDeleteConfirm(null) }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"><Pencil className="h-3.5 w-3.5" /></button>
                              <button onClick={() => { setDeleteConfirm(t.id); setMode('none') }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-red-50 hover:text-red-500" title="Delete template (does not affect accounts that copied from it)"><Trash2 className="h-3.5 w-3.5" /></button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-neutral-50/50">
                        <td colSpan={6} className="px-3 py-3">
                          <TemplateValuesEditor template={t} onError={setError} />
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        )}
        <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-2 text-[11px] text-neutral-500">
          {filtered.length} of {templates.length} template{templates.length !== 1 ? 's' : ''}
        </div>
      </div>
    </section>
  )
}

function TemplateForm({
  title, initial, existingCodes, codeEditable, isActive, onSave, onCancel, onToggleActive,
}: {
  title: string
  initial: TemplateFormData
  existingCodes: string[]
  codeEditable: boolean
  isActive?: boolean
  onSave: (d: TemplateFormData) => void
  onCancel: () => void
  onToggleActive?: (v: boolean) => void
}) {
  const [form, setForm] = useState<TemplateFormData>(initial)
  const [errors, setErrors] = useState<Partial<Record<keyof TemplateFormData, string>>>({})
  const set = (k: keyof TemplateFormData, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const validate = () => {
    const e: typeof errors = {}
    if (codeEditable) {
      if (!form.factor_code.trim()) e.factor_code = 'Required'
      else if (!/^[A-Za-z0-9_-]+$/.test(form.factor_code.trim())) e.factor_code = 'Letters, digits, _ or - only'
      else if (existingCodes.includes(form.factor_code.trim().toLowerCase())) e.factor_code = 'Code already in use'
    }
    if (!form.factor_name.trim()) e.factor_name = 'Required'
    setErrors(e)
    return Object.keys(e).length === 0
  }
  const fldCls = (err?: string) =>
    cn(
      'h-10 w-full rounded-md border bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]',
      err ? 'border-red-500' : 'border-neutral-300',
    )

  return (
    <div className="mb-4 rounded-lg border border-primary-200 bg-primary-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Code <span className="text-red-600">*</span>
            {!codeEditable && <span className="ml-1 text-[10px] text-neutral-400">(immutable)</span>}
          </label>
          <input
            value={form.factor_code}
            onChange={(e) => set('factor_code', e.target.value)}
            placeholder="e.g. brand, channel"
            className={fldCls(errors.factor_code)}
            disabled={!codeEditable}
          />
          {errors.factor_code && <p className="text-xs text-red-600">{errors.factor_code}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Name <span className="text-red-600">*</span></label>
          <input
            value={form.factor_name}
            onChange={(e) => set('factor_name', e.target.value)}
            placeholder="e.g. Brand"
            className={fldCls(errors.factor_name)}
          />
          {errors.factor_name && <p className="text-xs text-red-600">{errors.factor_name}</p>}
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <label className="text-xs font-medium text-neutral-700">Description (optional)</label>
          <input
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="What does this factor break a budget down by?"
            className={fldCls()}
          />
        </div>
        {!codeEditable && onToggleActive && (
          <div className="sm:col-span-2 flex items-center gap-2">
            <input
              type="checkbox"
              id="tmpl-active"
              checked={isActive ?? true}
              onChange={(e) => onToggleActive(e.target.checked)}
              className="h-4 w-4 rounded border-neutral-300"
            />
            <label htmlFor="tmpl-active" className="text-sm text-neutral-700">Active</label>
          </div>
        )}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
        <button
          onClick={() => { if (validate()) onSave(form) }}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#064A4A]"
        >
          <Check className="h-3.5 w-3.5" />Save
        </button>
      </div>
    </div>
  )
}

function TemplateValuesEditor({
  template, onError,
}: {
  template: FactorTemplate
  onError: (msg: string) => void
}) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editForm, setEditForm] = useState<{ value_code: string; value_name: string }>({ value_code: '', value_name: '' })

  const createValueMut = useMutation({
    mutationFn: (body: { value_code: string; value_name: string; sort_order: number }) =>
      budgetApi.post(`/factor-templates/${template.id}/values`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => onError(e.message),
  })
  const updateValueMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { value_name?: string; is_active?: boolean } }) =>
      budgetApi.patch(`/factor-template-values/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => onError(e.message),
  })
  const deleteValueMut = useMutation({
    mutationFn: (id: string) => budgetApi.delete(`/factor-template-values/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor-templates'] }),
    onError: (e: any) => onError(e.message),
  })

  return (
    <div className="flex flex-col gap-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
        Values ({template.values.length})
      </p>
      <ul className="flex flex-col gap-1">
        {template.values.map((v) => (
          <li key={v.id} className="flex items-center gap-2 rounded border border-neutral-200 bg-white px-2.5 py-1.5">
            <span className="inline-flex items-center rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-neutral-700">{v.value_code}</span>
            <span className="flex-1 text-sm text-neutral-800">{v.value_name}</span>
            <button
              onClick={() => updateValueMut.mutate({ id: v.id, body: { is_active: !v.is_active } })}
              className={cn('rounded-full px-2 py-0.5 text-[10px] font-medium', v.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}
              title="Click to toggle"
            >
              {v.is_active ? 'Active' : 'Inactive'}
            </button>
            <button
              onClick={() => { if (confirm(`Delete value "${v.value_code}"?`)) deleteValueMut.mutate(v.id) }}
              className="flex h-6 w-6 items-center justify-center rounded text-neutral-400 hover:bg-red-50 hover:text-red-500"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </li>
        ))}
        {!template.values.length && (
          <li className="text-xs text-neutral-400 italic">No values yet — add one below.</li>
        )}
      </ul>
      {adding ? (
        <div className="mt-1 flex items-end gap-2">
          <div className="flex flex-col gap-0.5">
            <label className="text-[10px] font-medium text-neutral-600">Code</label>
            <input
              value={editForm.value_code}
              onChange={(e) => setEditForm((p) => ({ ...p, value_code: e.target.value }))}
              placeholder="BRAND_A"
              className="h-8 w-32 rounded border border-neutral-300 px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[#085E5E]"
            />
          </div>
          <div className="flex flex-1 flex-col gap-0.5">
            <label className="text-[10px] font-medium text-neutral-600">Name</label>
            <input
              value={editForm.value_name}
              onChange={(e) => setEditForm((p) => ({ ...p, value_name: e.target.value }))}
              placeholder="Brand A"
              className="h-8 w-full rounded border border-neutral-300 px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[#085E5E]"
            />
          </div>
          <button
            onClick={() => {
              if (!editForm.value_code.trim() || !editForm.value_name.trim()) {
                onError('Value code and name are required')
                return
              }
              createValueMut.mutate(
                { value_code: editForm.value_code.trim(), value_name: editForm.value_name.trim(), sort_order: template.values.length },
                { onSuccess: () => { setEditForm({ value_code: '', value_name: '' }); setAdding(false) } },
              )
            }}
            className="flex h-8 items-center gap-1 rounded bg-[#085E5E] px-2.5 text-xs font-medium text-white hover:bg-[#064A4A]"
          >
            <Check className="h-3 w-3" />Add
          </button>
          <button onClick={() => { setAdding(false); setEditForm({ value_code: '', value_name: '' }) }} className="flex h-8 w-8 items-center justify-center rounded border border-neutral-300 text-neutral-500 hover:bg-neutral-50">
            <X className="h-3 w-3" />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="self-start inline-flex items-center gap-1 rounded border border-dashed border-neutral-300 px-2.5 py-1 text-xs text-neutral-600 hover:border-primary-400 hover:text-primary-700"
        >
          <Plus className="h-3 w-3" />Add Value
        </button>
      )}
    </div>
  )
}
