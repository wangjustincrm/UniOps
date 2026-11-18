/**
 * Factor Library — reusable decomposition factor templates.
 *
 * Source of truth for templates that can be cloned into a Budget Account's
 * decomposition factors. Library lives in budget-api (`/factor-templates`);
 * EPMS catalog page reads from here when an admin clicks "From Library…" on
 * an account.
 */
import { Fragment, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Layers, Plus, Pencil, Trash2, X, Check, Search,
  ChevronDown, ChevronRight, AlertCircle,
} from 'lucide-react'
import { Button, Checkbox, Input, SortTh, TBody, THead, Table, Td, Th, Tr, useSortableRows, Badge } from '@uniops/shell'
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

  // Loaded whole. Each template and its expanded values editor share one keyed
  // Fragment, so a re-sort moves the open editor with its own template.
  const tplSort = useSortableRows(filtered, {
    code: (t) => t.factor_code, name: (t) => t.factor_name,
    values: (t) => t.values.filter((v) => v.is_active).length,
    status: (t) => (t.is_active ? 'Active' : 'Inactive'),
  })
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
    <section className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          <Layers className="h-4 w-4" />
        </div>
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-fg">Factor Templates</h2>
          <p className="mt-0.5 text-xs text-fg-muted leading-relaxed">
            Templates are reusable starting points. Selecting one from a Budget Account copies its values into that
            account&rsquo;s factors — later template edits do not propagate to previously attached accounts.
            Managed by System Admin, Finance Manager, and Finance BP.
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-3 flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertCircle className="h-4 w-4" /> {error}
          <Button aria-label="Dismiss" variant="subtle" size="icon-xs" onClick={() => setError('')} className="ml-auto text-danger-fg hover:bg-danger-soft">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 flex-wrap">
          <div className="relative min-w-52">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            <Input
              placeholder="Search by code or name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-9 pr-3"
            />
          </div>
          <label className="flex items-center gap-1.5 text-xs text-fg-secondary">
            <Checkbox
             
              checked={showActiveOnly}
              onChange={(e) => setShowActiveOnly(e.target.checked)}
             
            />
            Active only
          </label>
        </div>
        <Button size="md"
          onClick={() => { setError(''); setMode('add') }}
          disabled={mode !== 'none'}
         
        >
          <Plus className="h-4 w-4" />Add Template
        </Button>
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

      <div className="rounded-lg border border-line overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-fg-muted">Loading templates…</div>
        ) : !filtered.length ? (
          <div className="py-10 text-center text-sm text-fg-muted">
            {templates.length === 0
              ? 'No factor templates yet — click "Add Template" to create your first one.'
              : 'No templates match the current filters.'}
          </div>
        ) : (
          <Table density="compact">
            <THead>
              <Tr className="bg-surface-subtle">
                <Th className="w-8" />
                <SortTh label="Code" sortKey="code" sort={tplSort.sort} onSort={tplSort.onSort} className="px-3 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                <SortTh label="Name" sortKey="name" sort={tplSort.sort} onSort={tplSort.onSort} className="px-3 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                <SortTh label="Values" sortKey="values" sort={tplSort.sort} onSort={tplSort.onSort} align="center" className="px-3 py-2.5 text-2xs font-semibold text-fg-muted w-20" />
                <SortTh label="Status" sortKey="status" sort={tplSort.sort} onSort={tplSort.onSort} className="px-3 py-2.5 text-left text-2xs font-semibold text-fg-muted w-24" />
                <Th className="w-24" />
              </Tr>
            </THead>
            <TBody>
              {tplSort.rows.map((t, i) => {
                const isOpen = expanded.has(t.id)
                const activeValues = t.values.filter((v) => v.is_active).length
                return (
                  <Fragment key={t.id}>
                    <Tr className={cn('border-b border-line-subtle', i % 2 === 1 ? 'bg-surface-subtle' : 'bg-surface')}>
                      <Td>
                        <Button variant="subtle" size="xs" onClick={() => toggleExpanded(t.id)}>
                          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </Button>
                      </Td>
                      <Td><Badge size="sm" className="font-mono">{t.factor_code}</Badge></Td>
                      <Td className="font-medium text-fg">
                        <div className="flex flex-col">
                          <span>{t.factor_name}</span>
                          {t.description && <span className="text-2xs text-fg-muted">{t.description}</span>}
                        </div>
                      </Td>
                      <Td align="center">
                        <span className={cn('inline-flex items-center justify-center rounded-full w-7 h-7 text-xs font-semibold', activeValues > 0 ? 'bg-primary-50 text-primary-700' : 'bg-surface-sunken text-fg-muted')}>
                          {activeValues}
                        </span>
                      </Td>
                      <Td>
                        <Badge size="sm" variant={t.is_active ? 'success' : 'neutral'}>
                          {t.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </Td>
                      <Td>
                        <div className="flex items-center justify-end gap-1">
                          {deleteConfirm === t.id ? (
                            <>
                              <Button aria-label="Confirm delete" variant="subtle" size="icon-xs" onClick={() => { deleteMut.mutate(t.id); setDeleteConfirm(null) }} className="text-danger-fg hover:bg-danger-soft" title="Confirm delete"><Check className="h-3.5 w-3.5" /></Button>
                              <Button aria-label="Cancel delete" variant="subtle" size="icon-xs" onClick={() => setDeleteConfirm(null)}><X className="h-3.5 w-3.5" /></Button>
                            </>
                          ) : (
                            <>
                              <Button aria-label="Edit" variant="subtle" size="icon-xs" onClick={() => { setError(''); setMode({ edit: t.id }); setDeleteConfirm(null) }}><Pencil className="h-3.5 w-3.5" /></Button>
                              <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" onClick={() => { setDeleteConfirm(t.id); setMode('none') }} title="Delete template (does not affect accounts that copied from it)"><Trash2 className="h-3.5 w-3.5" /></Button>
                            </>
                          )}
                        </div>
                      </Td>
                    </Tr>
                    {isOpen && (
                      <Tr className="bg-surface-subtle/50">
                        <Td colSpan={6}>
                          <TemplateValuesEditor template={t} onError={setError} />
                        </Td>
                      </Tr>
                    )}
                  </Fragment>
                )
              })}
            </TBody>
          </Table>
        )}
        <div className="border-t border-line bg-surface-subtle px-4 py-2 text-2xs text-fg-muted">
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

  return (
    <div className="mb-4 rounded-lg border border-primary-200 bg-primary-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <Button aria-label="Close" variant="subtle" size="icon-xs" onClick={onCancel}><X className="h-4 w-4" /></Button>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Code <span className="text-danger-fg">*</span>
            {!codeEditable && <span className="ml-1 text-2xs text-fg-muted">(immutable)</span>}
          </label>
          <Input
            value={form.factor_code}
            onChange={(e) => set('factor_code', e.target.value)}
            placeholder="e.g. brand, channel"
            error={!!errors.factor_code}
            disabled={!codeEditable}
          />
          {errors.factor_code && <p className="text-xs text-danger-fg">{errors.factor_code}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Name <span className="text-danger-fg">*</span></label>
          <Input
            value={form.factor_name}
            onChange={(e) => set('factor_name', e.target.value)}
            placeholder="e.g. Brand"
            error={!!errors.factor_name}
          />
          {errors.factor_name && <p className="text-xs text-danger-fg">{errors.factor_name}</p>}
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <label className="text-xs font-medium text-fg-secondary">Description (optional)</label>
          <Input
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="What does this factor break a budget down by?"
          />
        </div>
        {!codeEditable && onToggleActive && (
          <div className="sm:col-span-2 flex items-center gap-2">
            <Checkbox
             
              id="tmpl-active"
              checked={isActive ?? true}
              onChange={(e) => onToggleActive(e.target.checked)}
             
            />
            <label htmlFor="tmpl-active" className="text-sm text-fg-secondary">Active</label>
          </div>
        )}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm"
          onClick={() => { if (validate()) onSave(form) }}
         
        >
          <Check className="h-3.5 w-3.5" />Save
        </Button>
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
      <p className="text-2xs font-semibold text-fg-muted">
        Values ({template.values.length})
      </p>
      <ul className="flex flex-col gap-1">
        {template.values.map((v) => (
          <li key={v.id} className="flex items-center gap-2 rounded border border-line bg-surface px-2.5 py-1.5">
            <Badge size="sm" className="font-mono">{v.value_code}</Badge>
            <span className="flex-1 text-sm text-fg">{v.value_name}</span>
            <Button
              variant="subtle"
              size="xs"
              aria-pressed={v.is_active}
              onClick={() => updateValueMut.mutate({ id: v.id, body: { is_active: !v.is_active } })}
              className={cn('h-auto rounded-full px-2 py-0.5 text-2xs', v.is_active ? 'bg-success-soft text-success-fg hover:bg-success-soft' : 'bg-surface-sunken text-fg-muted')}
              title="Click to toggle"
            >
              {v.is_active ? 'Active' : 'Inactive'}
            </Button>
            <Button variant="danger-ghost" size="icon-xs" aria-label="Remove"
              onClick={() => { if (confirm(`Delete value "${v.value_code}"?`)) deleteValueMut.mutate(v.id) }}
             
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </li>
        ))}
        {!template.values.length && (
          <li className="text-xs text-fg-muted italic">No values yet — add one below.</li>
        )}
      </ul>
      {adding ? (
        <div className="mt-1 flex items-end gap-2">
          <div className="flex flex-col gap-0.5">
            <label className="text-2xs font-medium text-fg-secondary">Code</label>
            <Input inputSize="sm"
              value={editForm.value_code}
              onChange={(e) => setEditForm((p) => ({ ...p, value_code: e.target.value }))}
              placeholder="BRAND_A"
              className="w-32"
            />
          </div>
          <div className="flex flex-1 flex-col gap-0.5">
            <label className="text-2xs font-medium text-fg-secondary">Name</label>
            <Input inputSize="sm"
              value={editForm.value_name}
              onChange={(e) => setEditForm((p) => ({ ...p, value_name: e.target.value }))}
              placeholder="Brand A"
              className="w-full"
            />
          </div>
          <Button size="xs"
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
           
          >
            <Check className="h-3 w-3" />Add
          </Button>
          <Button aria-label="Cancel" variant="secondary" size="icon-sm" onClick={() => { setAdding(false); setEditForm({ value_code: '', value_name: '' }) }}>
            <X className="h-3 w-3" />
          </Button>
        </div>
      ) : (
        <Button variant="secondary" size="xs"
          onClick={() => setAdding(true)}
          className="self-start"
        >
          <Plus className="h-3 w-3" />Add Value
        </Button>
      )}
    </div>
  )
}
