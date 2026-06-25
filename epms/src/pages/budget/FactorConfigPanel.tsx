/**
 * Factor + FactorValue configuration panel — embedded inline under a budget account
 * in BudgetCatalogPage. Shows the current factor list and supports add/edit/delete.
 */
import { useState, useMemo } from 'react'
import { Plus, Trash2, X, Save, Pencil, RotateCcw, Check, Library } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  useFactors, useCreateFactor, useUpdateFactor, useDeleteFactor,
  useCreateFactorValue, useUpdateFactorValue, useDeleteFactorValue,
  useFactorTemplates, useCreateFactorFromTemplate,
} from '@/hooks/useBudget'
import type { ApiFactor, ApiFactorTemplate, ApiFactorValue } from '@/services/budget'

interface Props {
  accountId: string
  canEdit: boolean
}

/** Hard upper limit — must match budget-api MAX_FACTORS_PER_ACCOUNT. */
const MAX_FACTORS = 3

/** Role label for sort_order position in the matrix layout. */
const ROLE_BY_INDEX = [
  { label: 'Primary · Rows',     bg: 'bg-primary-50',  fg: 'text-primary-700',  icon: '🔻' },
  { label: '2nd · Columns',      bg: 'bg-blue-50',     fg: 'text-blue-700',     icon: '➡' },
  { label: '3rd · Sub-Columns',  bg: 'bg-purple-50',   fg: 'text-purple-700',   icon: '⬇' },
] as const

export function FactorConfigPanel({ accountId, canEdit }: Props) {
  const { data: factorsRaw = [], isLoading } = useFactors(accountId)
  const [showAdd, setShowAdd] = useState(false)
  const [showPicker, setShowPicker] = useState(false)

  // Sort by sort_order so the role badges line up with the matrix layout.
  const factors = useMemo(() => {
    return [...factorsRaw].sort(
      (a, b) => a.sort_order - b.sort_order || a.factor_code.localeCompare(b.factor_code),
    )
  }, [factorsRaw])

  const activeCount = factors.filter((f) => f.is_active).length
  const atLimit = activeCount >= MAX_FACTORS
  const grandFathered = factors.length > MAX_FACTORS

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Decomposition Factors ({activeCount} / {MAX_FACTORS})
        </h3>
        {canEdit && !showAdd && !showPicker && (
          <div className="flex items-center gap-1">
            <Button
              size="sm" variant="ghost"
              onClick={() => setShowPicker(true)}
              disabled={atLimit}
              title={atLimit
                ? 'Max 3 factors per Account. Deactivate one first.'
                : 'Pick a factor from the shared Factor Library'}
            >
              <Library className="h-3.5 w-3.5" /> From Library
            </Button>
            <Button
              size="sm" variant="ghost"
              onClick={() => setShowAdd(true)}
              disabled={atLimit}
              title={atLimit
                ? 'Max 3 factors per Account (Rows × Columns × Sub-Columns). Deactivate one first.'
                : 'Add a new factor inline'}
            >
              <Plus className="h-3.5 w-3.5" /> Add Factor
            </Button>
          </div>
        )}
      </div>

      {grandFathered && (
        <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-[11px] text-amber-800 leading-snug">
          This Account has {factors.length} factors. The Matrix editor uses the first 3 by sort order;
          additional factors are preserved but not editable. Deactivate or remove extras to clean up.
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-neutral-400">Loading…</p>
      ) : (
        <div className="flex flex-col gap-2">
          {factors.map((f, idx) => (
            <FactorCard
              key={f.id}
              factor={f}
              canEdit={canEdit}
              role={idx < MAX_FACTORS ? ROLE_BY_INDEX[idx] : null}
              outOfMatrix={idx >= MAX_FACTORS}
            />
          ))}
          {showAdd && canEdit && (
            <NewFactorForm
              accountId={accountId}
              onClose={() => setShowAdd(false)}
            />
          )}
          {showPicker && canEdit && (
            <FactorLibraryPicker
              accountId={accountId}
              existingCodes={factors.map((f) => f.factor_code.toLowerCase())}
              onClose={() => setShowPicker(false)}
            />
          )}
          {factors.length === 0 && !showAdd && (
            <p className="text-xs text-neutral-400 px-2 py-1">No factors configured yet.</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Factor card ──────────────────────────────────────────────────────────────

function FactorCard({
  factor, canEdit, role, outOfMatrix,
}: {
  factor: ApiFactor
  canEdit: boolean
  role: { label: string; bg: string; fg: string; icon: string } | null
  outOfMatrix: boolean
}) {
  const updateMut = useUpdateFactor()
  const deleteMut = useDeleteFactor()
  const createValMut = useCreateFactorValue()
  const [newValueCode, setNewValueCode] = useState('')
  const [newValueName, setNewValueName] = useState('')
  const [valueError, setValueError] = useState<string | null>(null)

  const handleAddValue = async () => {
    if (!newValueCode.trim() || !newValueName.trim()) return
    setValueError(null)
    const requestedCode = newValueCode.trim().toUpperCase()
    // Detect duplicate-with-deactivated up front so we can surface a useful hint
    // instead of the generic "already exists" message from the backend.
    const dup = factor.values.find((v) => v.value_code === requestedCode)
    if (dup && !dup.is_active) {
      setValueError(
        `Code "${requestedCode}" exists but was deactivated. Click the ↺ icon on its chip to reactivate, then rename via ✎.`,
      )
      return
    }
    try {
      await createValMut.mutateAsync({
        factorId: factor.id,
        body: { value_code: requestedCode, value_name: newValueName.trim() },
      })
      setNewValueCode(''); setNewValueName('')
    } catch (e: unknown) {
      setValueError(e instanceof Error ? e.message : 'Add failed')
    }
  }

  const handleToggleActive = () => {
    updateMut.mutate({ id: factor.id, body: { is_active: !factor.is_active } })
  }

  const handleDeleteFactor = () => {
    if (confirm(`Delete factor "${factor.factor_name}"? This is blocked if any plan breakdowns reference it.`)) {
      deleteMut.mutate(factor.id)
    }
  }

  return (
    <div className={cn(
      'rounded-lg border p-3',
      outOfMatrix ? 'border-amber-200 bg-amber-50/40' : 'border-neutral-200 bg-white',
    )}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          {role && (
            <span
              className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold', role.bg, role.fg)}
              title={`This factor is the ${role.label} axis in the matrix editor`}
            >
              <span aria-hidden>{role.icon}</span>{role.label}
            </span>
          )}
          {outOfMatrix && (
            <span className="text-[10px] rounded-full bg-amber-100 text-amber-700 px-2 py-0.5">Out of matrix</span>
          )}
          <span className="font-mono text-xs text-neutral-500">{factor.factor_code}</span>
          <span className="text-sm font-medium text-neutral-900">{factor.factor_name}</span>
          {!factor.is_active && (
            <span className="text-[10px] rounded-full bg-neutral-100 text-neutral-500 px-2 py-0.5">Inactive</span>
          )}
        </div>
        {canEdit && (
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={handleToggleActive}>
              {factor.is_active ? 'Deactivate' : 'Activate'}
            </Button>
            <Button size="icon-sm" variant="ghost" onClick={handleDeleteFactor} title="Delete factor">
              <Trash2 className="h-3.5 w-3.5 text-danger-600" />
            </Button>
          </div>
        )}
      </div>

      {/* Values list */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        {factor.values.length === 0 && (
          <p className="text-xs text-neutral-400">No values yet</p>
        )}
        {factor.values.map((v) => (
          <FactorValueChip key={v.id} value={v} canEdit={canEdit} />
        ))}
      </div>

      {/* Add value inline form */}
      {canEdit && (
        <div className="flex items-end gap-2">
          <div className="flex-1 grid grid-cols-2 gap-2">
            <input
              value={newValueCode}
              onChange={(e) => setNewValueCode(e.target.value)}
              placeholder="VALUE_CODE"
              className="h-8 text-xs rounded border border-neutral-300 px-2 font-mono"
            />
            <input
              value={newValueName}
              onChange={(e) => setNewValueName(e.target.value)}
              placeholder="Value name"
              className="h-8 text-xs rounded border border-neutral-300 px-2"
            />
          </div>
          <Button size="sm" variant="secondary" onClick={handleAddValue} disabled={createValMut.isPending}>
            <Plus className="h-3.5 w-3.5" /> Value
          </Button>
        </div>
      )}
      {valueError && <p className="text-xs text-danger-600 mt-1">{valueError}</p>}
    </div>
  )
}

// ── New factor inline form ──────────────────────────────────────────────────

function NewFactorForm({ accountId, onClose }: { accountId: string; onClose: () => void }) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const createMut = useCreateFactor()

  const handleSubmit = async () => {
    if (!code.trim() || !name.trim()) {
      setError('Both code and name are required')
      return
    }
    setError(null)
    try {
      await createMut.mutateAsync({
        accountId,
        body: { factor_code: code.trim().toLowerCase(), factor_name: name.trim() },
      })
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Create failed')
    }
  }

  return (
    <div className="rounded-lg border border-primary-300 bg-primary-50/30 p-3">
      <p className="text-xs font-semibold text-primary-700 mb-2">New Factor</p>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-neutral-600">Factor Code</label>
          <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="brand" className="h-8 text-xs font-mono" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-neutral-600">Factor Name</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Brand" className="h-8 text-xs" />
        </div>
      </div>
      {error && <p className="text-xs text-danger-600 mt-1">{error}</p>}
      <div className="flex items-center justify-end gap-2 mt-2">
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button size="sm" onClick={handleSubmit} disabled={createMut.isPending}>
          <Save className="h-3.5 w-3.5" />
          {createMut.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

// ── Single value chip with inline rename + reactivate ────────────────────────
//
// Why inline edit rather than delete + recreate: factor value codes are stable
// references stored in plan_breakdowns.factor_combo JSONB. Renaming the
// value_name only affects display; the code stays the same so historical
// breakdowns keep pointing at the right value.
//
// Reactivate covers the common "deactivated by mistake" recovery — the only
// alternative without it is a DB hand-edit, since the unique constraint
// (factor_id, value_code) means you can't re-add a deactivated code.

function FactorValueChip({ value, canEdit }: { value: ApiFactorValue; canEdit: boolean }) {
  const updateMut = useUpdateFactorValue()
  const deleteMut = useDeleteFactorValue()
  const [editing, setEditing] = useState(false)
  const [draftName, setDraftName] = useState(value.value_name)

  const startEdit = () => {
    setDraftName(value.value_name)
    setEditing(true)
  }
  const cancelEdit = () => {
    setEditing(false)
    setDraftName(value.value_name)
  }
  const saveEdit = () => {
    const trimmed = draftName.trim()
    if (!trimmed || trimmed === value.value_name) {
      cancelEdit()
      return
    }
    updateMut.mutate(
      { id: value.id, body: { value_name: trimmed } },
      { onSettled: () => setEditing(false) },
    )
  }
  const reactivate = () => updateMut.mutate({ id: value.id, body: { is_active: true } })

  if (editing) {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-md border border-primary-300 bg-primary-50 pl-2 pr-1 py-0.5 text-xs">
        <span className="font-mono text-neutral-500">{value.value_code}</span>
        <input
          autoFocus
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); saveEdit() }
            else if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
          }}
          className="h-5 text-xs rounded border border-neutral-300 bg-white px-1.5 min-w-32"
          disabled={updateMut.isPending}
        />
        <button
          onClick={saveEdit}
          disabled={updateMut.isPending}
          className="rounded p-0.5 hover:bg-primary-100 text-primary-700 disabled:opacity-50"
          title="Save (Enter)"
        >
          <Check className="h-3 w-3" />
        </button>
        <button
          onClick={cancelEdit}
          disabled={updateMut.isPending}
          className="rounded p-0.5 hover:bg-neutral-200 text-neutral-500"
          title="Cancel (Esc)"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    )
  }

  return (
    <div className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 pl-2 pr-1 py-0.5 text-xs">
      <span className="font-mono text-neutral-500">{value.value_code}</span>
      <span className={value.is_active ? 'text-neutral-700' : 'text-neutral-400 line-through'}>
        {value.value_name}
      </span>
      {canEdit && value.is_active && (
        <button
          onClick={startEdit}
          className="rounded p-0.5 hover:bg-neutral-200 text-neutral-500"
          title="Rename value"
        >
          <Pencil className="h-3 w-3" />
        </button>
      )}
      {canEdit && !value.is_active && (
        <button
          onClick={reactivate}
          disabled={updateMut.isPending}
          className="rounded p-0.5 hover:bg-primary-100 text-primary-600"
          title="Reactivate value"
        >
          <RotateCcw className="h-3 w-3" />
        </button>
      )}
      {canEdit && value.is_active && (
        <button
          onClick={() => deleteMut.mutate(value.id)}
          disabled={deleteMut.isPending}
          className="rounded p-0.5 hover:bg-neutral-200 text-neutral-500"
          title="Deactivate value"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </div>
  )
}

// ── From Library picker ──────────────────────────────────────────────────────

function FactorLibraryPicker({
  accountId, existingCodes, onClose,
}: {
  accountId: string
  existingCodes: string[]
  onClose: () => void
}) {
  const { data: templates = [], isLoading } = useFactorTemplates(true)
  const cloneMut = useCreateFactorFromTemplate()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [overrideCode, setOverrideCode] = useState('')
  const [overrideName, setOverrideName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const selected = templates.find((t) => t.id === selectedId) ?? null
  const effectiveCode = (overrideCode || selected?.factor_code || '').trim().toLowerCase()
  const codeCollides =
    selected !== null && effectiveCode !== '' && existingCodes.includes(effectiveCode)

  const handleAttach = async () => {
    if (!selected) return
    if (codeCollides) {
      setError(`This account already has a factor with code "${effectiveCode}". Override the code below or remove the existing one.`)
      return
    }
    setError(null)
    try {
      await cloneMut.mutateAsync({
        accountId,
        body: {
          template_id: selected.id,
          factor_code: overrideCode.trim() || undefined,
          factor_name: overrideName.trim() || undefined,
        },
      })
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Attach failed')
    }
  }

  return (
    <div className="rounded-lg border border-primary-300 bg-primary-50/30 p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold text-primary-700">Pick from Factor Library</p>
        <button onClick={onClose} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {isLoading ? (
        <p className="text-xs text-neutral-400">Loading templates…</p>
      ) : templates.length === 0 ? (
        <p className="text-xs text-neutral-500 italic">
          The Factor Library is empty. Ask an admin to add a template in
          Portal &rarr; Finance &rarr; Factor Library.
        </p>
      ) : (
        <>
          <div className="max-h-44 overflow-y-auto rounded border border-neutral-200 bg-white">
            <ul>
              {templates.map((t) => {
                const codeUsedAlready = existingCodes.includes(t.factor_code.toLowerCase())
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(t.id)
                        setOverrideCode('')
                        setOverrideName('')
                        setError(null)
                      }}
                      className={cn(
                        'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-primary-50',
                        selectedId === t.id && 'bg-primary-100',
                      )}
                    >
                      <span className="inline-flex items-center rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-neutral-700">
                        {t.factor_code}
                      </span>
                      <span className="flex-1 font-medium text-neutral-800">{t.factor_name}</span>
                      <span className="text-[10px] text-neutral-500">
                        {t.values.filter((v) => v.is_active).length} values
                      </span>
                      {codeUsedAlready && (
                        <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700" title="This code already exists on this account — override the code below to attach anyway">
                          code in use
                        </span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>

          {selected && (
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-neutral-600">
                  Factor code on this account (override optional)
                </label>
                <Input
                  value={overrideCode}
                  onChange={(e) => setOverrideCode(e.target.value)}
                  placeholder={selected.factor_code}
                  className="h-8 text-xs font-mono"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-neutral-600">
                  Factor name on this account (override optional)
                </label>
                <Input
                  value={overrideName}
                  onChange={(e) => setOverrideName(e.target.value)}
                  placeholder={selected.factor_name}
                  className="h-8 text-xs"
                />
              </div>
            </div>
          )}

          {selected && (
            <p className="mt-2 text-[10px] text-neutral-500 leading-relaxed">
              {selected.values.filter((v) => v.is_active).length} active value(s) will be copied:
              {' '}
              {selected.values.filter((v) => v.is_active).map((v) => v.value_code).join(', ') || '(none)'}.
              Once attached, this account&rsquo;s factor is independent of the template — later
              template edits do not propagate.
            </p>
          )}
        </>
      )}

      {error && <p className="text-xs text-danger-600 mt-2">{error}</p>}

      <div className="flex items-center justify-end gap-2 mt-3">
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button
          size="sm"
          onClick={handleAttach}
          disabled={!selected || cloneMut.isPending || codeCollides}
        >
          <Check className="h-3.5 w-3.5" />
          {cloneMut.isPending ? 'Attaching…' : 'Attach Factor'}
        </Button>
      </div>
    </div>
  )
}
