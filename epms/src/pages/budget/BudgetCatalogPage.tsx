import { useState, useRef, useMemo } from 'react'
import {
  ChevronDown, ChevronRight, Plus, Upload, Download, Edit2, Trash2, X,
  Power, Settings as SettingsIcon, Save, AlertTriangle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  useBudgetL1List, useCreateBudgetL1, useUpdateBudgetL1, useDeleteBudgetL1,
  useCreateBudgetAccount, useUpdateBudgetAccount, useDeleteBudgetAccount,
  useImportBudget,
} from '@/hooks/useBudget'
import type { ApiBudgetL1, ApiBudgetAccount } from '@/services/budget'
import { useAuthStore } from '@/stores/auth.store'
import { BUDGET_BASE } from '@/lib/api'
import { FactorConfigPanel } from './FactorConfigPanel'

const WRITE_ROLES = new Set(['system_admin', 'finance_manager', 'finance_bp'])

export default function BudgetCatalogPage() {
  const { user, token } = useAuthStore()
  const canEdit = !!user && WRITE_ROLES.has(user.role)
  const [showInactive, setShowInactive] = useState(false)
  const { data: l1List = [], isLoading } = useBudgetL1List({
    is_active: showInactive ? undefined : true,
    include_accounts: true,
  })
  const [selectedL1Id, setSelectedL1Id] = useState<string | null>(null)
  const [showL1Form, setShowL1Form] = useState(false)
  const [editingL1, setEditingL1] = useState<ApiBudgetL1 | null>(null)
  const [showAccountForm, setShowAccountForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<ApiBudgetAccount | null>(null)
  const [expandedAccountId, setExpandedAccountId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const importBudget = useImportBudget()
  const deleteL1Mut = useDeleteBudgetL1()
  const handleDeleteL1 = (l1: ApiBudgetL1) => {
    if (confirm(`Permanently delete L1 category "${l1.code} — ${l1.name}" and ALL its accounts?\nThis cannot be undone. Only allowed if no account under it is used in a budget plan or actuals.`)) {
      deleteL1Mut.mutate(l1.id, {
        onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Delete failed'),
        onSuccess: () => setSelectedL1Id(null),
      })
    }
  }

  const selectedL1 = useMemo(
    () => l1List.find((l1) => l1.id === selectedL1Id) ?? null,
    [l1List, selectedL1Id],
  )

  const handleExport = () => {
    const url = `${BUDGET_BASE}/api/v1/catalog/export`
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => r.blob())
      .then((blob) => {
        const downloadUrl = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = downloadUrl
        a.download = 'budget-catalog.csv'
        a.click()
        URL.revokeObjectURL(downloadUrl)
      })
  }

  const handleImportClick = () => fileInputRef.current?.click()

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    importBudget.mutate(file)
  }

  return (
    <div className="flex flex-col gap-4 p-6 h-full">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Budget Account Catalog</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            Shared L1/L2 account template — applies to all Cost Centers
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-neutral-600">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="h-4 w-4 rounded border-neutral-300 text-primary-600"
            />
            Show inactive
          </label>
          {canEdit && (
            <>
              <input
                ref={fileInputRef} type="file" accept=".csv"
                className="hidden" onChange={handleImportFile}
              />
              <Button variant="secondary" size="sm" onClick={handleImportClick} disabled={importBudget.isPending}>
                <Upload className="h-4 w-4" />
                {importBudget.isPending ? 'Importing…' : 'Import CSV'}
              </Button>
              <Button variant="secondary" size="sm" onClick={handleExport}>
                <Download className="h-4 w-4" />
                Export CSV
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Import result banner */}
      {importBudget.data && (
        <div className="rounded-md border border-success-200 bg-success-50 px-4 py-3 text-sm text-success-700">
          Import complete: {importBudget.data.l1_created} L1 created, {importBudget.data.l1_updated} updated,{' '}
          {importBudget.data.accounts_created} accounts created, {importBudget.data.accounts_updated} updated
          {importBudget.data.errors.length > 0 && (
            <div className="mt-2 text-xs text-danger-700">
              {importBudget.data.errors.length} errors:
              <ul className="ml-4 list-disc">
                {importBudget.data.errors.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
      {importBudget.isError && (
        <div className="rounded-md border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          <AlertTriangle className="inline h-4 w-4 mr-1" />
          Import failed: {String(importBudget.error)}
        </div>
      )}

      {/* Body: 2-column layout */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 flex-1 min-h-0">
        {/* ─ Left: L1 list ─ */}
        <aside className="md:col-span-4 lg:col-span-3 rounded-xl border border-neutral-200 bg-white overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-200">
            <h2 className="text-sm font-semibold text-neutral-800">L1 Categories</h2>
            {canEdit && (
              <Button size="icon-sm" variant="ghost" onClick={() => { setEditingL1(null); setShowL1Form(true) }} title="New L1">
                <Plus className="h-4 w-4" />
              </Button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto">
            {isLoading ? (
              <p className="px-4 py-6 text-sm text-neutral-400">Loading…</p>
            ) : l1List.length === 0 ? (
              <p className="px-4 py-6 text-sm text-neutral-400">No L1 categories yet</p>
            ) : (
              <ul>
                {l1List.map((l1) => (
                  <li key={l1.id}>
                    <button
                      onClick={() => setSelectedL1Id(l1.id)}
                      className={cn(
                        'w-full flex items-center justify-between px-4 py-2.5 text-left text-sm border-l-2 transition-colors',
                        selectedL1Id === l1.id
                          ? 'bg-primary-50 border-l-primary-600 text-primary-700 font-medium'
                          : 'border-l-transparent text-neutral-700 hover:bg-neutral-50',
                        !l1.is_active && 'opacity-50',
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="font-mono text-xs text-neutral-500">{l1.code}</p>
                        <p className="truncate">{l1.name}</p>
                      </div>
                      <span className="text-xs text-neutral-400 ml-2 shrink-0">
                        {(l1.accounts ?? []).filter((a) => a.is_active).length}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* ─ Right: Account list for selected L1 ─ */}
        <main className="md:col-span-8 lg:col-span-9 rounded-xl border border-neutral-200 bg-white overflow-hidden flex flex-col">
          {selectedL1 ? (
            <>
              {/* L1 header */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-neutral-900">{selectedL1.name}</h2>
                    <span className="font-mono text-xs text-neutral-500">{selectedL1.code}</span>
                    {!selectedL1.is_active && (
                      <span className="text-xs rounded-full bg-neutral-100 px-2 py-0.5 text-neutral-500">Inactive</span>
                    )}
                  </div>
                  {selectedL1.description && (
                    <p className="text-xs text-neutral-500 mt-0.5">{selectedL1.description}</p>
                  )}
                </div>
                {canEdit && (
                  <div className="flex items-center gap-1">
                    <Button size="icon-sm" variant="ghost"
                      onClick={() => { setEditingL1(selectedL1); setShowL1Form(true) }}
                      title="Edit L1"
                    >
                      <Edit2 className="h-4 w-4" />
                    </Button>
                    <Button size="icon-sm" variant="ghost"
                      onClick={() => { setEditingAccount(null); setShowAccountForm(true) }}
                      title="New Account"
                    >
                      <Plus className="h-4 w-4" />
                    </Button>
                    <Button size="icon-sm" variant="ghost"
                      onClick={() => handleDeleteL1(selectedL1)} disabled={deleteL1Mut.isPending}
                      title="Delete L1 (and all its accounts, if never referenced)"
                    >
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </Button>
                  </div>
                )}
              </div>

              {/* Account list */}
              <div className="flex-1 overflow-y-auto">
                {(selectedL1.accounts ?? []).length === 0 ? (
                  <p className="px-5 py-8 text-sm text-neutral-400">No accounts in this L1 yet</p>
                ) : (
                  <ul className="divide-y divide-neutral-100">
                    {selectedL1.accounts!.map((a) => (
                      <AccountListRow
                        key={a.id}
                        account={a}
                        canEdit={canEdit}
                        expanded={expandedAccountId === a.id}
                        onToggle={() => setExpandedAccountId((prev) => (prev === a.id ? null : a.id))}
                        onEdit={() => { setEditingAccount(a); setShowAccountForm(true) }}
                      />
                    ))}
                  </ul>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center flex-1 text-neutral-400">
              <SettingsIcon className="h-12 w-12 mb-3 opacity-30" />
              <p className="text-sm">Select an L1 category to view its accounts</p>
            </div>
          )}
        </main>
      </div>

      {/* ─ Modal forms ─ */}
      {showL1Form && canEdit && (
        <L1FormModal
          existing={editingL1}
          onClose={() => { setShowL1Form(false); setEditingL1(null) }}
        />
      )}
      {showAccountForm && canEdit && selectedL1 && (
        <AccountFormModal
          l1Id={selectedL1.id}
          existing={editingAccount}
          onClose={() => { setShowAccountForm(false); setEditingAccount(null) }}
        />
      )}
    </div>
  )
}

// ── Account row with expandable factor config ────────────────────────────────

function AccountListRow({
  account, canEdit, expanded, onToggle, onEdit,
}: {
  account: ApiBudgetAccount
  canEdit: boolean
  expanded: boolean
  onToggle: () => void
  onEdit: () => void
}) {
  const deleteMut = useDeleteBudgetAccount()
  const updateMut = useUpdateBudgetAccount()
  const toggleActive = () => {
    updateMut.mutate({ id: account.id, body: { is_active: !account.is_active } })
  }
  const handleDelete = () => {
    if (confirm(`Permanently delete account "${account.code} — ${account.name}"?\nThis cannot be undone. Only accounts never used in a budget plan or actuals can be deleted.`)) {
      deleteMut.mutate(account.id, {
        onError: (err: unknown) => alert(err instanceof Error ? err.message : 'Delete failed'),
      })
    }
  }
  return (
    <li className={cn(!account.is_active && 'opacity-50')}>
      <div className="flex items-center px-5 py-3 hover:bg-neutral-50 transition-colors">
        <button
          onClick={onToggle}
          className="mr-2 p-1 rounded hover:bg-neutral-100"
          disabled={!account.decomposition_enabled}
          title={account.decomposition_enabled ? 'Show factors' : 'No decomposition'}
        >
          {account.decomposition_enabled
            ? (expanded ? <ChevronDown className="h-4 w-4 text-neutral-500" /> : <ChevronRight className="h-4 w-4 text-neutral-500" />)
            : <span className="block h-4 w-4" />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-neutral-600">{account.code}</span>
            <span className="text-sm font-medium text-neutral-900 truncate">{account.name}</span>
            {account.decomposition_enabled && (
              <span className="text-[10px] rounded-full bg-primary-50 text-primary-700 px-2 py-0.5 font-medium">
                Decomposition
              </span>
            )}
            {!account.is_active && (
              <span className="text-[10px] rounded-full bg-neutral-100 text-neutral-500 px-2 py-0.5">Inactive</span>
            )}
          </div>
          {account.description && (
            <p className="text-xs text-neutral-500 mt-0.5">{account.description}</p>
          )}
        </div>
        {canEdit && (
          <div className="flex items-center gap-1 shrink-0 ml-2">
            <Button size="icon-sm" variant="ghost" onClick={onEdit} title="Edit">
              <Edit2 className="h-3.5 w-3.5" />
            </Button>
            <Button
              size="icon-sm" variant="ghost" onClick={toggleActive} disabled={updateMut.isPending}
              title={account.is_active ? 'Deactivate' : 'Activate'}
            >
              <Power className={cn('h-3.5 w-3.5', !account.is_active && 'text-neutral-400')} />
            </Button>
            <Button
              size="icon-sm" variant="ghost" onClick={handleDelete} disabled={deleteMut.isPending}
              title="Delete (only if never referenced)"
            >
              <Trash2 className="h-3.5 w-3.5 text-red-500" />
            </Button>
          </div>
        )}
      </div>
      {expanded && account.decomposition_enabled && (
        <div className="bg-neutral-50 border-t border-neutral-100 px-5 py-4">
          <FactorConfigPanel accountId={account.id} canEdit={canEdit} />
        </div>
      )}
    </li>
  )
}

// ── L1 form modal ────────────────────────────────────────────────────────────

function L1FormModal({ existing, onClose }: { existing: ApiBudgetL1 | null; onClose: () => void }) {
  const [code, setCode] = useState(existing?.code ?? '')
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [sortOrder, setSortOrder] = useState<number>(existing?.sort_order ?? 0)
  const [isActive, setIsActive] = useState(existing?.is_active ?? true)
  const [error, setError] = useState<string | null>(null)
  const createMut = useCreateBudgetL1()
  const updateMut = useUpdateBudgetL1()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      if (existing) {
        await updateMut.mutateAsync({ id: existing.id, body: { name, description, sort_order: sortOrder, is_active: isActive } })
      } else {
        await createMut.mutateAsync({ code, name, description, sort_order: sortOrder })
      }
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <ModalShell title={existing ? `Edit L1 — ${existing.code}` : 'New L1 Category'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-5">
        <Field label="Code (immutable after creation)" required>
          <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
            disabled={!!existing} placeholder="MKT" maxLength={20} required
            pattern="[A-Za-z0-9_-]+" title="A-Z, 0-9, underscore, dash" />
        </Field>
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Marketing" required />
        </Field>
        <Field label="Description">
          <textarea value={description ?? ''} onChange={(e) => setDescription(e.target.value)}
            className="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm min-h-16 focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Sort order">
            <Input type="number" value={sortOrder} onChange={(e) => setSortOrder(Number(e.target.value || 0))} />
          </Field>
          {existing && (
            <Field label="Status">
              <label className="flex items-center gap-2 h-10">
                <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)}
                  className="h-4 w-4 rounded border-neutral-300 text-primary-600" />
                <span className="text-sm">Active</span>
              </label>
            </Field>
          )}
        </div>
        {error && <p className="text-sm text-danger-600">{error}</p>}
        <ModalActions onCancel={onClose} pending={createMut.isPending || updateMut.isPending} />
      </form>
    </ModalShell>
  )
}

// ── Account form modal ──────────────────────────────────────────────────────

function AccountFormModal({
  l1Id, existing, onClose,
}: { l1Id: string; existing: ApiBudgetAccount | null; onClose: () => void }) {
  const [code, setCode] = useState(existing?.code ?? '')
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [sortOrder, setSortOrder] = useState<number>(existing?.sort_order ?? 0)
  const [decomp, setDecomp] = useState(existing?.decomposition_enabled ?? false)
  const [isActive, setIsActive] = useState(existing?.is_active ?? true)
  const [error, setError] = useState<string | null>(null)
  const createMut = useCreateBudgetAccount()
  const updateMut = useUpdateBudgetAccount()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      if (existing) {
        await updateMut.mutateAsync({
          id: existing.id,
          body: { name, description, sort_order: sortOrder, decomposition_enabled: decomp, is_active: isActive },
        })
      } else {
        await createMut.mutateAsync({
          code, name, description, sort_order: sortOrder, l1_id: l1Id, decomposition_enabled: decomp,
        })
      }
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <ModalShell title={existing ? `Edit Account — ${existing.code}` : 'New Account'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-5">
        <Field label="Code (immutable after creation)" required>
          <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
            disabled={!!existing} placeholder="MKT-001" maxLength={50} required
            pattern="[A-Za-z0-9_-]+" />
        </Field>
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Digital Advertising" required />
        </Field>
        <Field label="Description">
          <textarea value={description ?? ''} onChange={(e) => setDescription(e.target.value)}
            className="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm min-h-16 focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Sort order">
            <Input type="number" value={sortOrder} onChange={(e) => setSortOrder(Number(e.target.value || 0))} />
          </Field>
          <Field label="Decomposition">
            <label className="flex items-center gap-2 h-10">
              <input type="checkbox" checked={decomp} onChange={(e) => setDecomp(e.target.checked)}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600" />
              <span className="text-sm">Enable factor breakdown</span>
            </label>
          </Field>
        </div>
        {existing && (
          <Field label="Status">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600" />
              <span className="text-sm">Active</span>
            </label>
          </Field>
        )}
        {error && <p className="text-sm text-danger-600">{error}</p>}
        <ModalActions onCancel={onClose} pending={createMut.isPending || updateMut.isPending} />
      </form>
    </ModalShell>
  )
}

// ── Shared modal primitives ─────────────────────────────────────────────────

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200">
          <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-neutral-700">
        {label} {required && <span className="text-danger-600">*</span>}
      </label>
      {children}
    </div>
  )
}

function ModalActions({ onCancel, pending }: { onCancel: () => void; pending: boolean }) {
  return (
    <div className="flex items-center justify-end gap-2 pt-2 border-t border-neutral-100">
      <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
      <Button type="submit" disabled={pending}>
        <Save className="h-4 w-4" />
        {pending ? 'Saving…' : 'Save'}
      </Button>
    </div>
  )
}
