/**
 * Factor + FactorValue configuration panel — embedded inline under a budget account
 * in BudgetCatalogPage. Shows the current factor list and supports add/edit/delete.
 */
import { useState, useMemo } from 'react'
import { Plus, Trash2, X, Save, Pencil, RotateCcw, Check, Library, Rows3, Columns3, CornerDownRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { useConfirmDialog } from '@/components/ui/ConfirmDialog'
import {
  useFactors, useCreateFactor, useUpdateFactor, useDeleteFactor,
  useCreateFactorValue, useUpdateFactorValue, useDeleteFactorValue,
  useFactorTemplates, useCreateFactorFromTemplate,
} from '@/hooks/useBudget'
import type { ApiFactor, ApiFactorTemplate, ApiFactorValue } from '@/services/budget'
import { MenuItem, Badge } from '@uniops/shell'

interface Props {
  accountId: string
  canEdit: boolean
}

/** Hard upper limit — must match budget-api MAX_FACTORS_PER_ACCOUNT. */
const MAX_FACTORS = 3

/** Role label for sort_order position in the matrix layout. */
const ROLE_BY_INDEX = [
  { label: 'Primary · Rows',     bg: 'bg-primary-50',  fg: 'text-primary-700',  icon: Rows3 },
  { label: '2nd · Columns',      bg: 'bg-info-soft',     fg: 'text-info-fg',     icon: Columns3 },
  { label: '3rd · Sub-Columns',  bg: 'bg-cat4-soft',   fg: 'text-cat4-fg',   icon: CornerDownRight },
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
        <h3 className="text-xs font-semibold text-fg-muted">
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
        <div className="rounded-md bg-warning-soft border border-warning-line px-3 py-2 text-2xs text-warning-fg leading-snug">
          This Account has {factors.length} factors. The Matrix editor uses the first 3 by sort order;
          additional factors are preserved but not editable. Deactivate or remove extras to clean up.
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-fg-muted">Loading…</p>
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
            <p className="text-xs text-fg-muted px-2 py-1">No factors configured yet.</p>
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
  role: (typeof ROLE_BY_INDEX)[number] | null
  outOfMatrix: boolean
}) {
  const updateMut = useUpdateFactor()
  const deleteMut = useDeleteFactor()
  const confirmDlg = useConfirmDialog()
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
        `Code "${requestedCode}" exists but was deactivated. Use the restore button on its chip to reactivate it, then rename it with the edit button.`,
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
    // Asked in-page (was the browser's confirm); a refusal shows in the dialog.
    confirmDlg.ask({
      title: 'Delete Factor',
      subtitle: factor.factor_name,
      message: `Delete factor "${factor.factor_name}"? This is blocked if any plan breakdowns reference it.`,
      confirmLabel: 'Delete',
      failureFallback: 'Delete failed',
      onConfirm: () => deleteMut.mutateAsync(factor.id),
    })
  }

  return (
    <div className={cn(
      'rounded-lg border p-3',
      outOfMatrix ? 'border-warning-line bg-warning-soft/40' : 'border-line bg-surface',
    )}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          {role && (
            <span
              className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-semibold', role.bg, role.fg)}
              title={`This factor is the ${role.label} axis in the matrix editor`}
            >
              <role.icon aria-hidden className="h-3 w-3" />{role.label}
            </span>
          )}
          {outOfMatrix && (
            <Badge variant="warning" size="sm">Out of matrix</Badge>
          )}
          <span className="font-mono text-xs text-fg-muted">{factor.factor_code}</span>
          <span className="text-sm font-medium text-fg">{factor.factor_name}</span>
          {!factor.is_active && (
            <Badge size="sm">Inactive</Badge>
          )}
        </div>
        {canEdit && (
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={handleToggleActive}>
              {factor.is_active ? 'Deactivate' : 'Activate'}
            </Button>
            <Button aria-label="Delete factor" size="icon-sm" variant="ghost" onClick={handleDeleteFactor} title="Delete factor">
              <Trash2 className="h-3.5 w-3.5 text-danger-fg" />
            </Button>
          </div>
        )}
      </div>

      {/* Values list */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        {factor.values.length === 0 && (
          <p className="text-xs text-fg-muted">No values yet</p>
        )}
        {factor.values.map((v) => (
          <FactorValueChip key={v.id} value={v} canEdit={canEdit} />
        ))}
      </div>

      {/* Add value inline form */}
      {canEdit && (
        <div className="flex items-end gap-2">
          <div className="flex-1 grid grid-cols-2 gap-2">
            <Input inputSize="sm"
              value={newValueCode}
              onChange={(e) => setNewValueCode(e.target.value)}
              placeholder="VALUE_CODE"
              className="font-mono"
            />
            <Input inputSize="sm"
              value={newValueName}
              onChange={(e) => setNewValueName(e.target.value)}
              placeholder="Value name"
             
            />
          </div>
          <Button size="sm" variant="secondary" onClick={handleAddValue} disabled={createValMut.isPending}>
            <Plus className="h-3.5 w-3.5" /> Value
          </Button>
        </div>
      )}
      {valueError && <p className="text-xs text-danger-fg mt-1">{valueError}</p>}
      {confirmDlg.dialog}
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
          <label className="text-2xs font-medium text-fg-secondary">Factor Code</label>
          <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="brand" inputSize="sm" className="font-mono" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-2xs font-medium text-fg-secondary">Factor Name</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Brand" inputSize="sm" />
        </div>
      </div>
      {error && <p className="text-xs text-danger-fg mt-1">{error}</p>}
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
        <span className="font-mono text-fg-muted">{value.value_code}</span>
        <Input inputSize="sm"
          autoFocus
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); saveEdit() }
            else if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
          }}
          className="min-w-32"
          disabled={updateMut.isPending}
        />
        <Button aria-label="Save (Enter)" variant="ghost" size="icon-xs"
          onClick={saveEdit}
          disabled={updateMut.isPending}
         
          title="Save (Enter)"
        >
          <Check className="h-3 w-3" />
        </Button>
        <Button aria-label="Cancel (Esc)" variant="subtle" size="icon-xs"
          onClick={cancelEdit}
          disabled={updateMut.isPending}
         
          title="Cancel (Esc)"
        >
          <X className="h-3 w-3" />
        </Button>
      </div>
    )
  }

  return (
    <div className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface-subtle pl-2 pr-1 py-0.5 text-xs">
      <span className="font-mono text-fg-muted">{value.value_code}</span>
      <span className={value.is_active ? 'text-fg-secondary' : 'text-fg-muted line-through'}>
        {value.value_name}
      </span>
      {canEdit && value.is_active && (
        <Button aria-label="Rename value" variant="subtle" size="icon-xs"
          onClick={startEdit}
         
          title="Rename value"
        >
          <Pencil className="h-3 w-3" />
        </Button>
      )}
      {canEdit && !value.is_active && (
        <Button aria-label="Reactivate value" variant="ghost" size="icon-xs"
          onClick={reactivate}
          disabled={updateMut.isPending}
         
          title="Reactivate value"
        >
          <RotateCcw className="h-3 w-3" />
        </Button>
      )}
      {canEdit && value.is_active && (
        <Button aria-label="Deactivate value" variant="subtle" size="icon-xs"
          onClick={() => deleteMut.mutate(value.id)}
          disabled={deleteMut.isPending}
         
          title="Deactivate value"
        >
          <X className="h-3 w-3" />
        </Button>
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
        <Button aria-label="Close" variant="subtle" size="icon-xs" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {isLoading ? (
        <p className="text-xs text-fg-muted">Loading templates…</p>
      ) : templates.length === 0 ? (
        <p className="text-xs text-fg-muted italic">
          The Factor Library is empty. Ask an admin to add a template in
          Portal &rarr; Finance &rarr; Factor Library.
        </p>
      ) : (
        <>
          <div className="max-h-44 overflow-y-auto rounded border border-line bg-surface">
            <ul>
              {templates.map((t) => {
                const codeUsedAlready = existingCodes.includes(t.factor_code.toLowerCase())
                return (
                  <li key={t.id}>
                    <MenuItem
                      active={selectedId === t.id}
                      aria-pressed={selectedId === t.id}
                      onClick={() => {
                        setSelectedId(t.id)
                        setOverrideCode('')
                        setOverrideName('')
                        setError(null)
                      }}
                      className="px-2.5 py-1.5 text-xs"
                    >
                      <span className="inline-flex items-center rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-2xs font-semibold text-fg-secondary">
                        {t.factor_code}
                      </span>
                      <span className="flex-1 font-medium text-fg">{t.factor_name}</span>
                      <span className="text-2xs text-fg-muted">
                        {t.values.filter((v) => v.is_active).length} values
                      </span>
                      {codeUsedAlready && (
                        <Badge variant="warning" size="sm" title="This code already exists on this account — override the code below to attach anyway">
                          code in use
                        </Badge>
                      )}
                    </MenuItem>
                  </li>
                )
              })}
            </ul>
          </div>

          {selected && (
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-2xs font-medium text-fg-secondary">
                  Factor code on this account (override optional)
                </label>
                <Input
                  value={overrideCode}
                  onChange={(e) => setOverrideCode(e.target.value)}
                  placeholder={selected.factor_code}
                  inputSize="sm" className="font-mono"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-2xs font-medium text-fg-secondary">
                  Factor name on this account (override optional)
                </label>
                <Input
                  value={overrideName}
                  onChange={(e) => setOverrideName(e.target.value)}
                  placeholder={selected.factor_name}
                  inputSize="sm"
                />
              </div>
            </div>
          )}

          {selected && (
            <p className="mt-2 text-2xs text-fg-muted leading-relaxed">
              {selected.values.filter((v) => v.is_active).length} active value(s) will be copied:
              {' '}
              {selected.values.filter((v) => v.is_active).map((v) => v.value_code).join(', ') || '(none)'}.
              Once attached, this account&rsquo;s factor is independent of the template — later
              template edits do not propagate.
            </p>
          )}
        </>
      )}

      {error && <p className="text-xs text-danger-fg mt-2">{error}</p>}

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
