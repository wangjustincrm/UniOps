/**
 * Chart of Accounts configuration (Phase a A1.5)
 *
 * - Account management: browse / create / edit / deactivate / delete
 *   (delete only for never-referenced accounts; referenced → deactivate)
 * - Auxiliary dimensions per account, each one Optional or Required
 *   (1:1 with the posting_lines dimension columns)
 * - CSV import/export (system-wide convention): export, edit in Excel,
 *   import back — upsert by code, never deletes. `dim*` marks required.
 * - Mappings: line_role and budget_account bridges
 *
 * List styling follows the Portal convention (BudgetConfigPage cost centers):
 * neutral palette, zebra rows, code chips, status pills, inline delete confirm.
 */
import { useMemo, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle, BookOpenCheck, Check, Download, Loader2, Pencil, Plus,
  RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-react'
import { SortTh, useSortableRows, Checkbox, FileInput, Input, Select, TBody, THead, Table, Td, Th, Tr, Button, Modal as ShellModal, Badge } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, financeDownload, financeUpload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { CoaSyncModal, type CoaSyncStatus } from './CoaSyncModal'

const ACCOUNT_TYPES = ['asset', 'liability', 'equity', 'revenue', 'expense'] as const
const TYPE_LABEL: Record<string, string> = {
  asset: 'Asset', liability: 'Liability', equity: 'Equity',
  revenue: 'Revenue', expense: 'Expense',
}
interface AuxDim { code: string; required: boolean }
interface AuxType {
  id: string; code: string; name: string; master_source: string | null
  storage: string; builtin: boolean; is_active: boolean; sort_order: number
}

interface Account {
  id: string
  code: string
  name: string
  account_type: string
  subtype: string | null
  normal_balance: string
  is_postable: boolean
  parent_code: string | null
  is_active: boolean
  aux_dimensions: AuxDim[]
  quantity_accounting: boolean
  default_uom: string | null
  default_currency: string | null
  effective_from: string | null
  effective_to: string | null
  cash_flow_category: string | null
  mnemonic: string | null
  is_off_balance: boolean
}

interface Mapping { id: string; mapping_type: string; source_code: string; account_code: string }

type AccountForm = Omit<Account, 'id'>

const EMPTY_FORM: AccountForm = {
  code: '', name: '', account_type: 'expense', subtype: null,
  normal_balance: 'debit', is_postable: true, parent_code: null,
  is_active: true, aux_dimensions: [],
  quantity_accounting: false, default_uom: null, default_currency: null,
  effective_from: null, effective_to: null, cash_flow_category: null,
  mnemonic: null, is_off_balance: false,
}

// ── Account edit/create modal ──────────────────────────────────────────────────

function AccountModal({ initial, isNew, auxTypes, onClose }: {
  initial: AccountForm; isNew: boolean; auxTypes: AuxType[]; onClose: () => void
}) {
  const qc = useQueryClient()
  const [form, setForm] = useState<AccountForm>(initial)
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: async () => {
      if (isNew) return financeApi.post<Account>('/coa', form)
      const { code: _c, ...patch } = form
      return financeApi.patch<Account>(`/coa/${initial.code}`, patch)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['coa'] }); onClose() },
    onError: (e: Error) => setError(e.message),
  })

  const set = (patch: Partial<AccountForm>) => setForm((f) => ({ ...f, ...patch }))
  const auxOf = (code: string) => form.aux_dimensions.find((d) => d.code === code)
  // Aux dimensions come from NC via coa_aux_items (the single source of truth
  // since 2026-07-15); hand-edits here would be silently overwritten on the
  // next NC Sync, so the picker below is display-only. The aux_dimensions
  // column and its API stay put — retiring them is a separate cleanup (§12).

  return (
    <ShellModal open onClose={onClose} title={<>{isNew ? 'New Account' : `Edit Account ${initial.code}`}</>} closeOnEscape={false} size="full" className="max-w-lg">
      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Code</span>
          <Input value={form.code} disabled={!isNew}
                 onChange={(e) => set({ code: e.target.value.trim() })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Parent</span>
          <Input value={form.parent_code ?? ''} placeholder="(top level)"
                 onChange={(e) => set({ parent_code: e.target.value.trim() || null })} />
        </label>
        <label className="col-span-2 text-sm">
          <span className="mb-1 block text-fg-secondary">Name</span>
          <Input value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Type</span>
          <Select value={form.account_type}
                  onChange={(e) => set({ account_type: e.target.value })}
                  className="w-full">
            {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </Select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Normal Balance</span>
          <Select value={form.normal_balance}
                  onChange={(e) => set({ normal_balance: e.target.value })}
                  className="w-full">
            <option value="debit">Debit</option>
            <option value="credit">Credit</option>
          </Select>
        </label>
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <Checkbox checked={form.is_postable}
                 onChange={(e) => set({ is_postable: e.target.checked })} />
          Postable (uncheck = summary/header account)
        </label>
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <Checkbox checked={form.is_active}
                 onChange={(e) => set({ is_active: e.target.checked })} />
          Active
        </label>

        {/* NC-parity metadata */}
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <Checkbox checked={form.quantity_accounting}
                 onChange={(e) => set({ quantity_accounting: e.target.checked })} />
          Quantity accounting
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Default UoM</span>
          <Input value={form.default_uom ?? ''} placeholder="e.g. kg"
                 onChange={(e) => set({ default_uom: e.target.value.trim() || null })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Default Currency</span>
          <Input value={form.default_currency ?? ''} placeholder="(entity default)"
                 onChange={(e) => set({ default_currency: e.target.value.trim().toUpperCase() || null })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Mnemonic</span>
          <Input value={form.mnemonic ?? ''}
                 onChange={(e) => set({ mnemonic: e.target.value.trim() || null })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Effective From</span>
          <Input type="date" value={form.effective_from ?? ''}
                 onChange={(e) => set({ effective_from: e.target.value || null })} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-fg-secondary">Cash Flow Category</span>
          <Input value={form.cash_flow_category ?? ''} placeholder="operating / investing / financing"
                 onChange={(e) => set({ cash_flow_category: e.target.value.trim() || null })} />
        </label>
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <Checkbox checked={form.is_off_balance}
                 onChange={(e) => set({ is_off_balance: e.target.checked })} />
          Off-balance-sheet
        </label>
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-medium text-fg-secondary">
          Auxiliary Dimensions
          <span className="ml-2 font-normal text-fg-muted">
            read-only — managed by NC Sync
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {auxTypes.map((d) => {
            const state = auxOf(d.code)
            return (
              // Read-only display (NC Sync owns it), so a plain element, not a disabled button.
              <div key={d.code} aria-disabled="true"
                      title="Managed by NC Sync — read-only"
                      className={cn(
                        'flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-sm cursor-not-allowed opacity-70',
                        !state && 'border-line text-fg-muted',
                        state && !state.required && 'border-accent-line bg-accent-soft text-accent-fg',
                        state?.required && 'border-accent bg-accent text-fg-on-accent',
                      )}>
                <span className="flex items-center gap-1.5">
                  {state && <Check className="h-3.5 w-3.5" />}
                  {d.name}
                </span>
                {state && (
                  <span className={cn('text-2xs font-semibold',
                                      state.required ? 'text-fg-on-accent' : 'text-accent-fg')}>
                    {state.required ? 'Required' : 'Optional'}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {error && (
        <div className="mt-3 flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertCircle className="h-4 w-4" /> {error}
        </div>
      )}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" size="md" onClick={onClose}>Cancel</Button>
        <Button size="md" onClick={() => save.mutate()}
                disabled={save.isPending || !form.code || !form.name}
                >
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save
        </Button>
      </div>
    </ShellModal>
  )
}

// ── Mappings tab ───────────────────────────────────────────────────────────────

function MappingsTab({ accounts, canManage }: { accounts: Account[]; canManage: boolean }) {
  const qc = useQueryClient()
  const { data: mappings = [] } = useQuery({
    queryKey: ['coa-mappings'],
    queryFn: () => financeApi.get<Mapping[]>('/coa/mappings'),
  })
  const postable = accounts.filter((a) => a.is_postable && a.is_active)
  const [newSrc, setNewSrc] = useState('')
  const [newAcct, setNewAcct] = useState('')
  const [error, setError] = useState<string | null>(null)

  const upsert = useMutation({
    mutationFn: ({ mtype, src, acct }: { mtype: string; src: string; acct: string }) =>
      financeApi.put<Mapping>(`/coa/mappings/${mtype}/${encodeURIComponent(src)}`,
                              { account_code: acct }),
    onSuccess: () => { setError(null); setNewSrc(''); setNewAcct(''); qc.invalidateQueries({ queryKey: ['coa-mappings'] }) },
    onError: (e: Error) => setError(e.message),
  })

  const groups: [string, string, Mapping[]][] = [
    ['line_role', 'Posting slots → accounts (line_role — used by the payment executor)',
      mappings.filter((m) => m.mapping_type === 'line_role')],
    ['budget_account', 'Budget accounts → ledger accounts (budget_account bridge)',
      mappings.filter((m) => m.mapping_type === 'budget_account')],
  ]

  const acctSelect = (value: string, onChange: (v: string) => void) => (
    <Select value={value} disabled={!canManage} onChange={(e) => onChange(e.target.value)}
            selectSize="sm">
      <option value="">— Select account —</option>
      {postable.map((a) => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}
    </Select>
  )

  return (
    <div className="flex flex-col gap-6">
      {error && <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</div>}
      {groups.map(([mtype, title, rows]) => (
        <div key={mtype} className="rounded-lg border border-line bg-surface p-4">
          <h3 className="mb-3 text-2xs font-semibold text-fg-muted">{title}</h3>
          {rows.length === 0 && <div className="mb-2 text-sm text-fg-muted">No mappings yet.</div>}
          <div className="flex flex-col gap-2">
            {rows.map((m) => (
              <div key={m.id} className="flex items-center gap-3 text-sm">
                <Badge size="sm" className="w-48 font-mono">
                  {m.source_code}
                </Badge>
                <span className="text-fg-muted">→</span>
                {acctSelect(m.account_code, (v) =>
                  v && upsert.mutate({ mtype, src: m.source_code, acct: v }))}
              </div>
            ))}
            {mtype === 'budget_account' && canManage && (
              <div className="mt-2 flex items-center gap-3 border-t border-line-subtle pt-3 text-sm">
                <Input value={newSrc} onChange={(e) => setNewSrc(e.target.value)}
                       placeholder="Budget account code"
                       inputSize="sm" className="w-48" />
                <span className="text-fg-muted">→</span>
                {acctSelect(newAcct, setNewAcct)}
                <Button size="md" disabled={!newSrc || !newAcct || upsert.isPending}
                        onClick={() => upsert.mutate({ mtype, src: newSrc, acct: newAcct })}
                        className={cn('px-2.5 py-1')}>
                  <Plus className="h-4 w-4" /> Add
                </Button>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function CoaConfigPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [showCoaSync, setShowCoaSync] = useState(false)
  const { data: ncStatus } = useQuery({
    queryKey: ['coa-sync-status'],
    queryFn: () => financeApi.get<CoaSyncStatus>('/coa-sync/status'),
  })
  const [tab, setTab] = useState<'accounts' | 'mappings' | 'aux'>('accounts')
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [editing, setEditing] = useState<{ form: AccountForm; isNew: boolean } | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['coa', showInactive],
    queryFn: () => financeApi.get<Account[]>(`/coa?include_inactive=${showInactive}`),
    enabled: !!user,
  })
  // Server-side capability (JWT roles ∪ role_management assignments) —
  // never gate on jwt.role in the UI.
  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
    enabled: !!user,
  })
  const canManage = perms?.can_manage ?? false

  const { data: auxTypes = [] } = useQuery({
    queryKey: ['coa-aux-types'],
    queryFn: () => financeApi.get<AuxType[]>('/coa/aux-types'),
    enabled: !!user,
  })
  const auxLabel = (code: string) => auxTypes.find((t) => t.code === code)?.name ?? code

  const deleteMut = useMutation({
    mutationFn: (code: string) => financeApi.delete(`/coa/${code}`),
    onSuccess: () => { setBanner(null); qc.invalidateQueries({ queryKey: ['coa'] }) },
    onError: (e: Error) => setBanner({ kind: 'err', text: e.message }),
  })

  const onImportFile = async (file: File) => {
    setImportBusy(true); setBanner(null)
    try {
      const r = await financeUpload<{ inserted: number; updated: number; errors: string[] }>(
        '/coa/import', file)
      setBanner({
        kind: 'ok',
        text: `Import complete: ${r.inserted} created, ${r.updated} updated` +
          (r.errors.length ? ` · ${r.errors.length} rows skipped — ${r.errors[0]}` : ''),
      })
      qc.invalidateQueries({ queryKey: ['coa'] })
    } catch (e) {
      setBanner({ kind: 'err', text: e instanceof Error ? e.message : 'Import failed' })
    } finally {
      setImportBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const onExport = async () => {
    try {
      await financeDownload('/coa/export', 'chart-of-accounts.csv')
    } catch (e) {
      setBanner({ kind: 'err', text: e instanceof Error ? e.message : 'Export failed' })
    }
  }

  const filtered = useMemo(() => accounts.filter((a) =>
    (!typeFilter || a.account_type === typeFilter) &&
    (!search || a.code.includes(search) || a.name.toLowerCase().includes(search.toLowerCase())),
  ), [accounts, search, typeFilter])

  // Loaded whole. Rows keep a.id keys and the delete confirmation is by code,
  // so re-ordering never moves it. Default (null) = the API's code order.
  const acctSort = useSortableRows(filtered, {
    code: (a) => a.code, name: (a) => a.name, type: (a) => TYPE_LABEL[a.account_type],
    balance: (a) => (a.normal_balance === 'debit' ? 'Dr' : 'Cr'),
    aux: (a) => a.aux_dimensions.map((d) => auxLabel(d.code)).join(', '),
    status: (a) => (a.is_active ? 'Active' : 'Inactive'),
  })

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/coa"
      title="Chart of Accounts"
      subtitle="Accounts, auxiliary dimensions, and account mappings — export/import CSV to manage the chart wholesale"
      headerActions={ncStatus?.configured && ncStatus?.can_sync ? (
        <Button variant="secondary" size="md" onClick={() => setShowCoaSync(true)}
               >
          <RefreshCw className="h-4 w-4" /> NC Sync
        </Button>
      ) : undefined}
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          {([['accounts', 'Accounts'], ['mappings', 'Mappings'],
             ['aux', 'Aux Dimensions']] as const).map(([t, label]) => (
            <Button key={t} onClick={() => setTab(t)}
                    variant={tab === t ? 'primary' : 'secondary'} size="md" aria-pressed={tab === t}>
              {label}
            </Button>
          ))}
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-danger-soft text-danger-fg' : 'bg-success-soft text-success-fg')}>
            {banner.text}
          </div>
        )}

        {tab === 'aux' ? <AuxTypesTab auxTypes={auxTypes} canManage={canManage} /> :
         tab === 'mappings' ? <MappingsTab accounts={accounts} canManage={canManage} /> : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-fg-muted" />
                <Input value={search} onChange={(e) => setSearch(e.target.value)}
                       placeholder="Search code or name"
                       className="w-56 pl-8" />
              </div>
              <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
                      className="w-36">
                <option value="">All types</option>
                {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
              </Select>
              <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
                <Checkbox checked={showInactive}
                       onChange={(e) => setShowInactive(e.target.checked)} />
                Include inactive
              </label>
              <div className="ml-auto flex gap-2">
                <Button variant="secondary" size="md" onClick={onExport}>
                  <Download className="h-4 w-4" /> Export CSV
                </Button>
                {canManage && (
                  <>
                    <FileInput ref={fileInput} accept=".csv,text/csv" className="hidden"
                           onChange={(e) => e.target.files?.[0] && onImportFile(e.target.files[0])} />
                    <Button variant="secondary" size="md" onClick={() => fileInput.current?.click()} disabled={importBusy}
                            >
                      {importBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                      Import CSV
                    </Button>
                    <Button size="md" onClick={() => setEditing({ form: EMPTY_FORM, isNew: true })}
                            >
                      <Plus className="h-4 w-4" /> New Account
                    </Button>
                  </>
                )}
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-line">
              {isLoading ? (
                <div className="py-10 text-center text-sm text-fg-muted">Loading accounts…</div>
              ) : !filtered.length ? (
                <div className="py-10 text-center text-sm text-fg-muted">
                  <BookOpenCheck className="mx-auto mb-2 h-8 w-8" />No matching accounts.
                </div>
              ) : (
                <Table density="compact">
                  <THead>
                    <Tr className="bg-surface-subtle">
                      <SortTh label="Code" sortKey="code" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                      <SortTh label="Name" sortKey="name" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                      <SortTh label="Type" sortKey="type" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                      <SortTh label="Balance" sortKey="balance" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                      <SortTh label="Aux Dimensions" sortKey="aux" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
                      <SortTh label="Status" sortKey="status" sort={acctSort.sort} onSort={acctSort.onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted w-24" />
                      <Th className="w-24" />
                    </Tr>
                  </THead>
                  <TBody>
                    {acctSort.rows.map((a, i) => (
                      <Tr key={a.id}
                          className={cn('border-b border-line-subtle last:border-b-0',
                                        i % 2 === 1 ? 'bg-surface-subtle' : 'bg-surface',
                                        !a.is_active && 'opacity-50')}>
                        <Td className={cn('px-4 py-2.5', a.parent_code && 'pl-8')}>
                          <Badge size="sm" className="font-mono">
                            {a.code}
                          </Badge>
                        </Td>
                        <Td className={cn('px-4 py-2.5 text-fg', !a.is_postable && 'font-semibold')}>
                          {a.name}
                          {!a.is_postable && <span className="ml-2 text-2xs font-normal text-fg-muted">header</span>}
                        </Td>
                        <Td className="text-xs text-fg-secondary">{TYPE_LABEL[a.account_type]}</Td>
                        <Td className="text-xs text-fg-secondary">
                          {a.normal_balance === 'debit' ? 'Dr' : 'Cr'}
                        </Td>
                        <Td>
                          <div className="flex flex-wrap gap-1">
                            {a.aux_dimensions.map((d) => (
                              <Badge key={d.code} size="sm" variant={d.required ? 'solid' : 'accent'}
                                    title={d.required ? 'Required on postings' : 'Optional'}>
                                {auxLabel(d.code)}{d.required && '*'}
                              </Badge>
                            ))}
                          </div>
                        </Td>
                        <Td>
                          <Badge size="sm" variant={a.is_active ? 'success' : 'neutral'}>
                            {a.is_active ? 'Active' : 'Inactive'}
                          </Badge>
                        </Td>
                        <Td>
                          {canManage && (
                            <div className="flex items-center justify-end gap-1">
                              {deleteConfirm === a.code ? (
                                <>
                                  <Button aria-label="Confirm delete" variant="subtle" size="icon-xs" onClick={() => { deleteMut.mutate(a.code); setDeleteConfirm(null) }}
                                          className="text-danger-fg hover:bg-danger-soft">
                                    <Check className="h-3.5 w-3.5" />
                                  </Button>
                                  <Button aria-label="Cancel delete" variant="subtle" size="icon-xs" onClick={() => setDeleteConfirm(null)}
                                         >
                                    <X className="h-3.5 w-3.5" />
                                  </Button>
                                </>
                              ) : (
                                <>
                                  <Button aria-label="Edit" variant="subtle" size="icon-xs" onClick={() => { setEditing({ form: { ...a }, isNew: false }); setDeleteConfirm(null) }}
                                         >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </Button>
                                  <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" onClick={() => setDeleteConfirm(a.code)}
                                          title="Delete (only if never referenced)"
                                         >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </Button>
                                </>
                              )}
                            </div>
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              )}
              <div className="border-t border-line bg-surface-subtle px-4 py-2 text-2xs text-fg-muted">
                {filtered.length} of {accounts.length} account{accounts.length !== 1 ? 's' : ''}
              </div>
            </div>
          </>
        )}
      </div>

      {editing && (
        <AccountModal initial={editing.form} isNew={editing.isNew} auxTypes={auxTypes}
                      onClose={() => setEditing(null)} />
      )}

      {showCoaSync && (
        <CoaSyncModal onClose={() => setShowCoaSync(false)}
                      onSynced={() => setShowCoaSync(false)} />
      )}
    </PortalChromeLayout>
  )
}


// ── Aux dimension types tab (辅助核算项目录) ──────────────────────────────────────

function AuxTypesTab({ auxTypes, canManage }: { auxTypes: AuxType[]; canManage: boolean }) {
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => financeApi.post('/coa/aux-types', { code: code.trim(), name: name.trim() }),
    onSuccess: () => { setError(null); setCode(''); setName(''); qc.invalidateQueries({ queryKey: ['coa-aux-types'] }) },
    onError: (e: Error) => setError(e.message),
  })
  const toggle = useMutation({
    mutationFn: (t: AuxType) => financeApi.patch(`/coa/aux-types/${t.code}`, { is_active: !t.is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['coa-aux-types'] }),
  })

  const { rows, sort, onSort } = useSortableRows(auxTypes, {
    code: (t) => t.code, name: (t) => t.name,
    storage: (t) => (t.storage === 'column' ? 'spine column' : 'side table'),
    source: (t) => t.master_source, builtin: (t) => (t.builtin ? 'yes' : null),
    status: (t) => (t.is_active ? 'Active' : 'Inactive'),
  })

  return (
    <div className="overflow-hidden rounded-lg border border-line">
      {canManage && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-subtle p-3">
          <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="code (e.g. grant_program)"
                 className="w-56" />
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name"
                 className="w-64" />
          <Button size="md" disabled={!code.trim() || !name.trim() || create.isPending}
                  onClick={() => create.mutate()} >
            <Plus className="h-4 w-4" /> Add Dimension
          </Button>
          {error && <span className="text-sm text-danger-fg">{error}</span>}
        </div>
      )}
      <Table density="compact">
        <THead>
          <Tr className="bg-surface-subtle">
            <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <SortTh label="Storage" sortKey="storage" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <SortTh label="Source" sortKey="source" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <SortTh label="Built-in" sortKey="builtin" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-2.5 text-left text-2xs font-semibold text-fg-muted" />
            <Th />
          </Tr>
        </THead>
        <TBody>
          {rows.map((t, i) => (
            <Tr key={t.id} className={cn('border-b border-line-subtle last:border-0', i % 2 === 1 && 'bg-surface-subtle')}>
              <Td className="font-mono text-2xs">{t.code}</Td>
              <Td className="text-fg">{t.name}</Td>
              <Td className="text-xs text-fg-muted">
                {t.storage === 'column' ? 'spine column' : 'side table'}
              </Td>
              <Td className="text-xs text-fg-muted">{t.master_source ?? '—'}</Td>
              <Td className="text-xs text-fg-muted">{t.builtin ? 'yes' : ''}</Td>
              <Td>
                <Badge size="sm" variant={t.is_active ? 'success' : 'neutral'}>
                  {t.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </Td>
              <Td align="right">
                {canManage && !t.builtin && (
                  <Button variant="subtle" size="xs" onClick={() => toggle.mutate(t)}
                         >
                    {t.is_active ? 'Deactivate' : 'Activate'}
                  </Button>
                )}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </div>
  )
}
