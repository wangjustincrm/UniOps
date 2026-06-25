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
  Search, Trash2, Upload, X,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi, financeDownload, financeUpload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

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

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

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
  /** off → optional → required → off */
  const cycleAux = (code: string) => {
    const cur = auxOf(code)
    let next: AuxDim[]
    if (!cur) next = [...form.aux_dimensions, { code, required: false }]
    else if (!cur.required) next = form.aux_dimensions.map((d) => d.code === code ? { ...d, required: true } : d)
    else next = form.aux_dimensions.filter((d) => d.code !== code)
    set({ aux_dimensions: next })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-neutral-800">
            {isNew ? 'New Account' : `Edit Account ${initial.code}`}
          </h3>
          <button onClick={onClose}><X className="h-5 w-5 text-neutral-400" /></button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Code</span>
            <input value={form.code} disabled={!isNew}
                   onChange={(e) => set({ code: e.target.value.trim() })}
                   className={cn(inputCls, 'w-full disabled:bg-neutral-100')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Parent</span>
            <input value={form.parent_code ?? ''} placeholder="(top level)"
                   onChange={(e) => set({ parent_code: e.target.value.trim() || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="col-span-2 text-sm">
            <span className="mb-1 block text-neutral-600">Name</span>
            <input value={form.name} onChange={(e) => set({ name: e.target.value })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Type</span>
            <select value={form.account_type}
                    onChange={(e) => set({ account_type: e.target.value })}
                    className={cn(inputCls, 'w-full')}>
              {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Normal Balance</span>
            <select value={form.normal_balance}
                    onChange={(e) => set({ normal_balance: e.target.value })}
                    className={cn(inputCls, 'w-full')}>
              <option value="debit">Debit</option>
              <option value="credit">Credit</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.is_postable}
                   onChange={(e) => set({ is_postable: e.target.checked })} />
            Postable (uncheck = summary/header account)
          </label>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.is_active}
                   onChange={(e) => set({ is_active: e.target.checked })} />
            Active
          </label>

          {/* NC-parity metadata */}
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.quantity_accounting}
                   onChange={(e) => set({ quantity_accounting: e.target.checked })} />
            Quantity accounting
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Default UoM</span>
            <input value={form.default_uom ?? ''} placeholder="e.g. kg"
                   onChange={(e) => set({ default_uom: e.target.value.trim() || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Default Currency</span>
            <input value={form.default_currency ?? ''} placeholder="(entity default)"
                   onChange={(e) => set({ default_currency: e.target.value.trim().toUpperCase() || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Mnemonic</span>
            <input value={form.mnemonic ?? ''}
                   onChange={(e) => set({ mnemonic: e.target.value.trim() || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Effective From</span>
            <input type="date" value={form.effective_from ?? ''}
                   onChange={(e) => set({ effective_from: e.target.value || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-neutral-600">Cash Flow Category</span>
            <input value={form.cash_flow_category ?? ''} placeholder="operating / investing / financing"
                   onChange={(e) => set({ cash_flow_category: e.target.value.trim() || null })}
                   className={cn(inputCls, 'w-full')} />
          </label>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.is_off_balance}
                   onChange={(e) => set({ is_off_balance: e.target.checked })} />
            Off-balance-sheet
          </label>
        </div>

        <div className="mt-4">
          <div className="mb-2 text-sm font-medium text-neutral-700">
            Auxiliary Dimensions
            <span className="ml-2 font-normal text-neutral-400">
              click to cycle: off → optional → required
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {auxTypes.map((d) => {
              const state = auxOf(d.code)
              return (
                <button key={d.code} type="button" onClick={() => cycleAux(d.code)}
                        className={cn(
                          'flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-sm',
                          !state && 'border-neutral-200 text-neutral-500 hover:bg-neutral-50',
                          state && !state.required && 'border-primary-600/40 bg-primary-50 text-primary-700',
                          state?.required && 'border-[#085E5E] bg-[#085E5E] text-white',
                        )}>
                  <span className="flex items-center gap-1.5">
                    {state && <Check className="h-3.5 w-3.5" />}
                    {d.name}
                  </span>
                  {state && (
                    <span className={cn('text-[10px] font-semibold uppercase tracking-wide',
                                        state.required ? 'text-white/90' : 'text-primary-600')}>
                      {state.required ? 'Required' : 'Optional'}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>

        {error && (
          <div className="mt-3 flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className={secondaryBtn}>Cancel</button>
          <button onClick={() => save.mutate()}
                  disabled={save.isPending || !form.code || !form.name}
                  className={primaryBtn}>
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </div>
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
    <select value={value} disabled={!canManage} onChange={(e) => onChange(e.target.value)}
            className={cn(inputCls, 'h-8 py-0 disabled:bg-neutral-50')}>
      <option value="">— Select account —</option>
      {postable.map((a) => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}
    </select>
  )

  return (
    <div className="flex flex-col gap-6">
      {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {groups.map(([mtype, title, rows]) => (
        <div key={mtype} className="rounded-lg border border-neutral-200 bg-white p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">{title}</h3>
          {rows.length === 0 && <div className="mb-2 text-sm text-neutral-400">No mappings yet.</div>}
          <div className="flex flex-col gap-2">
            {rows.map((m) => (
              <div key={m.id} className="flex items-center gap-3 text-sm">
                <span className="inline-flex w-48 items-center rounded-md bg-neutral-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-neutral-700">
                  {m.source_code}
                </span>
                <span className="text-neutral-400">→</span>
                {acctSelect(m.account_code, (v) =>
                  v && upsert.mutate({ mtype, src: m.source_code, acct: v }))}
              </div>
            ))}
            {mtype === 'budget_account' && canManage && (
              <div className="mt-2 flex items-center gap-3 border-t border-neutral-100 pt-3 text-sm">
                <input value={newSrc} onChange={(e) => setNewSrc(e.target.value)}
                       placeholder="Budget account code"
                       className={cn(inputCls, 'h-8 w-48 py-0')} />
                <span className="text-neutral-400">→</span>
                {acctSelect(newAcct, setNewAcct)}
                <button disabled={!newSrc || !newAcct || upsert.isPending}
                        onClick={() => upsert.mutate({ mtype, src: newSrc, acct: newAcct })}
                        className={cn(primaryBtn, 'px-2.5 py-1')}>
                  <Plus className="h-4 w-4" /> Add
                </button>
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

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/coa"
      title="Chart of Accounts"
      subtitle="Accounts, auxiliary dimensions, and account mappings — export/import CSV to manage the chart wholesale"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          {([['accounts', 'Accounts'], ['mappings', 'Mappings'],
             ['aux', 'Aux Dimensions']] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn('rounded-lg px-4 py-2 text-sm font-medium',
                      tab === t ? 'bg-[#085E5E] text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {tab === 'aux' ? <AuxTypesTab auxTypes={auxTypes} canManage={canManage} /> :
         tab === 'mappings' ? <MappingsTab accounts={accounts} canManage={canManage} /> : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-neutral-400" />
                <input value={search} onChange={(e) => setSearch(e.target.value)}
                       placeholder="Search code or name"
                       className={cn(inputCls, 'w-56 pl-8')} />
              </div>
              <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
                      className={cn(inputCls, 'w-36')}>
                <option value="">All types</option>
                {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
              </select>
              <label className="flex items-center gap-1.5 text-sm text-neutral-600">
                <input type="checkbox" checked={showInactive}
                       onChange={(e) => setShowInactive(e.target.checked)} />
                Include inactive
              </label>
              <div className="ml-auto flex gap-2">
                <button onClick={onExport} className={secondaryBtn}>
                  <Download className="h-4 w-4" /> Export CSV
                </button>
                {canManage && (
                  <>
                    <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden"
                           onChange={(e) => e.target.files?.[0] && onImportFile(e.target.files[0])} />
                    <button onClick={() => fileInput.current?.click()} disabled={importBusy}
                            className={secondaryBtn}>
                      {importBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                      Import CSV
                    </button>
                    <button onClick={() => setEditing({ form: EMPTY_FORM, isNew: true })}
                            className={primaryBtn}>
                      <Plus className="h-4 w-4" /> New Account
                    </button>
                  </>
                )}
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-neutral-200">
              {isLoading ? (
                <div className="py-10 text-center text-sm text-neutral-400">Loading accounts…</div>
              ) : !filtered.length ? (
                <div className="py-10 text-center text-sm text-neutral-400">
                  <BookOpenCheck className="mx-auto mb-2 h-8 w-8" />No matching accounts.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-200 bg-neutral-50">
                      {['Code', 'Name', 'Type', 'Balance', 'Aux Dimensions', 'Status', ''].map((h, i) => (
                        <th key={i}
                            className={cn('px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500',
                                          i === 5 && 'w-24', i === 6 && 'w-24')}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((a, i) => (
                      <tr key={a.id}
                          className={cn('border-b border-neutral-100 last:border-b-0',
                                        i % 2 === 1 ? 'bg-neutral-50' : 'bg-white',
                                        !a.is_active && 'opacity-50')}>
                        <td className={cn('px-4 py-2.5', a.parent_code && 'pl-8')}>
                          <span className="inline-flex items-center rounded-md bg-neutral-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-neutral-700">
                            {a.code}
                          </span>
                        </td>
                        <td className={cn('px-4 py-2.5 text-neutral-800', !a.is_postable && 'font-semibold')}>
                          {a.name}
                          {!a.is_postable && <span className="ml-2 text-[11px] font-normal text-neutral-400">header</span>}
                        </td>
                        <td className="px-4 py-2.5 text-xs text-neutral-600">{TYPE_LABEL[a.account_type]}</td>
                        <td className="px-4 py-2.5 text-xs text-neutral-600">
                          {a.normal_balance === 'debit' ? 'Dr' : 'Cr'}
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex flex-wrap gap-1">
                            {a.aux_dimensions.map((d) => (
                              <span key={d.code}
                                    title={d.required ? 'Required on postings' : 'Optional'}
                                    className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                                      d.required ? 'bg-[#085E5E] text-white' : 'bg-primary-50 text-primary-700')}>
                                {auxLabel(d.code)}{d.required && '*'}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                            a.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                            {a.is_active ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          {canManage && (
                            <div className="flex items-center justify-end gap-1">
                              {deleteConfirm === a.code ? (
                                <>
                                  <button onClick={() => { deleteMut.mutate(a.code); setDeleteConfirm(null) }}
                                          className="flex h-7 w-7 items-center justify-center rounded text-red-600 hover:bg-red-50">
                                    <Check className="h-3.5 w-3.5" />
                                  </button>
                                  <button onClick={() => setDeleteConfirm(null)}
                                          className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100">
                                    <X className="h-3.5 w-3.5" />
                                  </button>
                                </>
                              ) : (
                                <>
                                  <button onClick={() => { setEditing({ form: { ...a }, isNew: false }); setDeleteConfirm(null) }}
                                          className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600">
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>
                                  <button onClick={() => setDeleteConfirm(a.code)}
                                          title="Delete (only if never referenced)"
                                          className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-red-50 hover:text-red-500">
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-2 text-[11px] text-neutral-500">
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

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200">
      {canManage && (
        <div className="flex flex-wrap items-center gap-2 border-b border-neutral-200 bg-neutral-50 p-3">
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="code (e.g. grant_program)"
                 className={cn(inputCls, 'w-56')} />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name"
                 className={cn(inputCls, 'w-64')} />
          <button disabled={!code.trim() || !name.trim() || create.isPending}
                  onClick={() => create.mutate()} className={primaryBtn}>
            <Plus className="h-4 w-4" /> Add Dimension
          </button>
          {error && <span className="text-sm text-red-600">{error}</span>}
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50">
            {['Code', 'Name', 'Storage', 'Source', 'Built-in', 'Status', ''].map((h, i) => (
              <th key={i} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {auxTypes.map((t, i) => (
            <tr key={t.id} className={cn('border-b border-neutral-100 last:border-0', i % 2 === 1 && 'bg-neutral-50')}>
              <td className="px-4 py-2.5 font-mono text-[11px]">{t.code}</td>
              <td className="px-4 py-2.5 text-neutral-800">{t.name}</td>
              <td className="px-4 py-2.5 text-xs text-neutral-500">
                {t.storage === 'column' ? 'spine column' : 'side table'}
              </td>
              <td className="px-4 py-2.5 text-xs text-neutral-500">{t.master_source ?? '—'}</td>
              <td className="px-4 py-2.5 text-xs text-neutral-400">{t.builtin ? 'yes' : ''}</td>
              <td className="px-4 py-2.5">
                <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                  t.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                  {t.is_active ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="px-4 py-2.5 text-right">
                {canManage && !t.builtin && (
                  <button onClick={() => toggle.mutate(t)}
                          className="text-xs text-neutral-400 hover:text-[#085E5E]">
                    {t.is_active ? 'Deactivate' : 'Activate'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
