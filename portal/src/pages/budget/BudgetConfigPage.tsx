import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check, Loader2, CheckCircle2, AlertCircle,
  Gauge, ShieldAlert, Layers, ExternalLink,
  Calendar, Plus, X, Search, Pencil, Trash2, Building2,
  Wallet, Upload, Download,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi, mdmApi, budgetApi, budgetUpload, EPMS_URL, encodeSession } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BudgetAdminConfig {
  yellow_threshold_pct: number
  red_threshold_pct: number
  over_budget_mode: 'fm_gm_opm' | 'fm_only' | 'hard_block'
  available_fiscal_years: number[]
}

interface CompanyConfigPartial {
  budget_admin_config?: Partial<BudgetAdminConfig>
}


const _currentYear = new Date().getUTCFullYear()
const DEFAULT_AVAILABLE_FISCAL_YEARS: number[] = [
  _currentYear - 1, _currentYear, _currentYear + 1, _currentYear + 2,
]

const DEFAULT_BUDGET_ADMIN_CONFIG: BudgetAdminConfig = {
  yellow_threshold_pct: 80,
  red_threshold_pct: 100,
  over_budget_mode: 'fm_gm_opm',
  available_fiscal_years: DEFAULT_AVAILABLE_FISCAL_YEARS,
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useCompanyConfig() {
  return useQuery<CompanyConfigPartial>({
    queryKey: ['portal-config'],
    queryFn: () => epmsApi.get<CompanyConfigPartial>('/config'),
  })
}

function useSaveCompanyConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<CompanyConfigPartial>) =>
      epmsApi.patch<CompanyConfigPartial>('/config', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-config'] }),
  })
}

// ── Reusable UI ───────────────────────────────────────────────────────────────

function SectionCard({
  icon: Icon, title, description, children,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          <Icon className="h-4.5 w-4.5" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
          <p className="mt-0.5 text-xs text-neutral-500 leading-relaxed">{description}</p>
        </div>
      </div>
      <div className="pl-12">{children}</div>
    </section>
  )
}

function Toast({ ok, msg }: { ok: boolean; msg: string }) {
  return (
    <div className={cn(
      'flex items-center gap-2 rounded-lg px-3 py-2 text-sm',
      ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700',
    )}>
      {ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
      {msg}
    </div>
  )
}

function SaveButton({ loading, label = 'Save Changes' }: { loading: boolean; label?: string }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60 transition-colors"
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
      {loading ? 'Saving…' : label}
    </button>
  )
}

// ── Fiscal Years editor ──────────────────────────────────────────────────────

function FiscalYearsEditor({
  years, onAdd, onRemove,
}: {
  years: number[]
  onAdd: (year: number) => void
  onRemove: (year: number) => void
}) {
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const n = Number(input.trim())
    if (!input.trim()) return
    if (!Number.isInteger(n) || n < 2000 || n > 2100) {
      setError('Year must be an integer between 2000 and 2100')
      return
    }
    if (years.includes(n)) {
      setError(`FY ${n} is already in the list`)
      return
    }
    onAdd(n)
    setInput('')
    setError(null)
  }

  const sorted = [...years].sort((a, b) => a - b)
  return (
    <div className="flex flex-col gap-3 max-w-xl">
      {/* Chips */}
      {sorted.length === 0 ? (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
          No fiscal years configured. Users won't be able to create budget plans until at least one year is added.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {sorted.map((y) => (
            <span
              key={y}
              className="inline-flex items-center gap-1.5 rounded-full border border-neutral-200 bg-neutral-50 pl-3 pr-1 py-1 text-xs font-medium text-neutral-700"
            >
              FY {y}
              <button
                type="button"
                onClick={() => onRemove(y)}
                className="ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-neutral-400 hover:bg-neutral-200 hover:text-red-600"
                aria-label={`Remove FY ${y}`}
                title={`Remove FY ${y}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Add row */}
      <div className="flex items-start gap-2">
        <div className="flex flex-col gap-1">
          <input
            type="number"
            min={2000} max={2100}
            placeholder="e.g. 2028"
            value={input}
            onChange={(e) => { setInput(e.target.value); setError(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
            className="h-9 w-32 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
          {error && <p className="text-[11px] text-red-600">{error}</p>}
        </div>
        <button
          type="button"
          onClick={submit}
          className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 h-9 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Year
        </button>
      </div>
      <p className="text-[11px] text-neutral-400">
        Removing a year only hides it from the dropdown — existing budget plans for that year remain accessible from Budget Plans.
      </p>
    </div>
  )
}

// ── 1. Fiscal Year + Thresholds + Over-Budget Mode (CompanyConfig) ────────────

function CompanyConfigSection() {
  const { data: cfg, isLoading } = useCompanyConfig()
  const save = useSaveCompanyConfig()
  const [form, setForm] = useState<BudgetAdminConfig>(DEFAULT_BUDGET_ADMIN_CONFIG)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  useEffect(() => {
    if (!cfg?.budget_admin_config) return
    // Merge stored partial onto defaults so legacy rows missing the new
    // available_fiscal_years field still render a sensible editor state.
    const stored = cfg.budget_admin_config
    setForm({
      ...DEFAULT_BUDGET_ADMIN_CONFIG,
      ...stored,
      available_fiscal_years:
        Array.isArray(stored.available_fiscal_years) && stored.available_fiscal_years.length > 0
          ? [...stored.available_fiscal_years].sort((a, b) => a - b)
          : DEFAULT_AVAILABLE_FISCAL_YEARS,
    })
  }, [cfg?.budget_admin_config])

  const set = <K extends keyof BudgetAdminConfig>(k: K, v: BudgetAdminConfig[K]) =>
    setForm((p) => ({ ...p, [k]: v }))

  const addYear = (year: number) => {
    if (!Number.isInteger(year) || year < 2000 || year > 2100) return
    setForm((p) => {
      if (p.available_fiscal_years.includes(year)) return p
      return {
        ...p,
        available_fiscal_years: [...p.available_fiscal_years, year].sort((a, b) => a - b),
      }
    })
  }

  const removeYear = (year: number) => {
    setForm((p) => ({
      ...p,
      available_fiscal_years: p.available_fiscal_years.filter((y) => y !== year),
    }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await save.mutateAsync({ budget_admin_config: form })
      setToast({ ok: true, msg: 'Budget Config saved.' })
      setTimeout(() => setToast(null), 3500)
    } catch (err: any) {
      setToast({ ok: false, msg: err.message ?? 'Save failed' })
    }
  }

  if (isLoading) {
    return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">

      {/* Available Fiscal Years */}
      <SectionCard
        icon={Calendar}
        title="Available Fiscal Years"
        description="Years offered in the New Budget Plan dropdown and plan-list year filter. Add a year before users need to create a plan for it (e.g. add 2028 in late 2027)."
      >
        <FiscalYearsEditor
          years={form.available_fiscal_years}
          onAdd={addYear}
          onRemove={removeYear}
        />
      </SectionCard>

      {/* Budget Alert Thresholds */}
      <SectionCard
        icon={Gauge}
        title="Budget Alert Thresholds"
        description="Controls the colour-coded budget utilisation alerts on the Budget Dashboard."
      >
        <div className="flex flex-col gap-3 max-w-2xl">
          {[
            {
              k: 'yellow_threshold_pct' as const, label: 'Warning threshold',
              badge: 'bg-amber-100 text-amber-700', badgeLabel: 'Yellow',
              desc: 'Budget row highlighted amber. Daily email digest sent to Dept. Manager.',
            },
            {
              k: 'red_threshold_pct' as const, label: 'Over-budget threshold',
              badge: 'bg-red-100 text-red-700', badgeLabel: 'Red',
              desc: 'Budget row highlighted red. Immediate email + Teams alert to Dept. Manager, Finance Manager, and GM/OPM.',
            },
          ].map(({ k, label, badge, badgeLabel, desc }) => (
            <div key={k} className="flex items-start justify-between gap-4 rounded-xl border border-neutral-200 bg-neutral-50 px-4 py-3.5">
              <div className="flex-1 min-w-0">
                <div className="mb-0.5 flex items-center gap-2">
                  <p className="text-sm font-medium text-neutral-900">{label}</p>
                  <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', badge)}>{badgeLabel}</span>
                </div>
                <p className="text-xs text-neutral-500">{desc}</p>
              </div>
              <div className="shrink-0 flex items-center gap-2">
                <input
                  type="number" min={1} max={200}
                  value={form[k]}
                  onChange={(e) =>
                    set(k, Math.min(200, Math.max(1, Number(e.target.value))))
                  }
                  className="h-9 w-20 rounded-lg border border-neutral-300 bg-white px-2 text-center text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary-600"
                />
                <span className="text-sm text-neutral-500">%</span>
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Over-Budget Approval Mode */}
      <SectionCard
        icon={ShieldAlert}
        title="Over-Budget Approval Mode"
        description="Determines what happens when a Purchase Request would push a budget account past its annual limit."
      >
        <div className="flex flex-col gap-3 max-w-2xl">
          {[
            {
              v: 'fm_gm_opm' as const, label: 'Finance Manager + GM/OPM',
              desc: 'Two sequential approvals required. Finance Manager verifies the budget impact; GM or OPM authorises the overspend.',
            },
            {
              v: 'fm_only' as const, label: 'Finance Manager only',
              desc: 'Single approval. Faster, but GM/OPM visibility is bypassed.',
            },
            {
              v: 'hard_block' as const, label: 'Hard block',
              desc: 'Submission is rejected outright if any budget account would exceed its annual limit. No approval path provided.',
            },
          ].map(({ v, label, desc }) => (
            <label
              key={v}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3.5 transition-colors',
                form.over_budget_mode === v
                  ? 'border-primary-300 bg-primary-50'
                  : 'border-neutral-200 bg-white hover:bg-neutral-50',
              )}
            >
              <input
                type="radio"
                checked={form.over_budget_mode === v}
                onChange={() => set('over_budget_mode', v)}
                className="mt-0.5 accent-primary-600 shrink-0"
              />
              <div>
                <p className="text-sm font-medium text-neutral-900">{label}</p>
                <p className="mt-0.5 text-xs text-neutral-500">{desc}</p>
              </div>
            </label>
          ))}
        </div>
      </SectionCard>

      {/* Save */}
      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── 2. Decomposition Settings (read-only info) ────────────────────────────────

function DecompositionSettingsSection() {
  return (
    <SectionCard
      icon={Layers}
      title="Factor Decomposition"
      description="Configuration of decomposition factors for Budget Accounts (the matrix axes used when filling monthly breakdowns)."
    >
      <div className="flex flex-col gap-3 max-w-2xl text-sm text-neutral-700">
        <p>
          Each Budget Account may have up to <strong>3 decomposition factors</strong>. Factor order in the
          catalog determines the matrix layout:
        </p>
        <ul className="ml-5 list-disc text-[13px] text-neutral-600 space-y-0.5">
          <li><strong>Primary factor</strong> → matrix <em>rows</em></li>
          <li><strong>2nd factor</strong> → matrix <em>columns</em></li>
          <li><strong>3rd factor</strong> → <em>sub-columns</em> within each column</li>
        </ul>
        <p className="text-[11px] text-neutral-500 leading-snug">
          The 3-factor cap is a hard limit enforced by budget-api at factor creation. Configure factors
          on the Account Catalog page.
        </p>
      </div>
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const CC_MANAGE_ROLES = new Set(['system_admin', 'finance_manager', 'ap_clerk'])

// ── 期初 (opening balance) import section ─────────────────────────────────────

const OPENING_ROLES = new Set(['system_admin', 'finance_manager'])
const OPENING_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface OpeningImportResult {
  rows_created: number
  rows_updated: number
  errors: string[]
}

interface OpeningBalanceRow {
  cost_center_id: string
  account_id: string
  account_code: string
  account_name: string
  fiscal_year: number
  month: number
  amount: number
}

interface OpeningListResponse {
  cost_center_id: string | null
  fiscal_year: number
  items: OpeningBalanceRow[]
}

function OpeningBalanceSection() {
  const qc = useQueryClient()
  const { data: cfg } = useCompanyConfig()
  const years = useMemo(() => {
    const ys = cfg?.budget_admin_config?.available_fiscal_years
    return Array.isArray(ys) && ys.length > 0
      ? [...ys].sort((a, b) => a - b)
      : DEFAULT_AVAILABLE_FISCAL_YEARS
  }, [cfg?.budget_admin_config])

  const { data: ccs = [] } = useQuery<ApiCostCenter[]>({
    queryKey: ['portal-cost-centers'],
    queryFn: () => mdmApi.get<ApiCostCenter[]>('/cost-centers'),
  })
  const activeCcs = useMemo(() => ccs.filter((c) => c.is_active), [ccs])

  const [ccId, setCcId] = useState('')
  // Default to the current fiscal year (not the highest configured year) so an
  // unchanged dropdown doesn't silently file imports under a far-future year.
  const [year, setYear] = useState<number>(
    years.includes(_currentYear) ? _currentYear : (years[years.length - 1] ?? _currentYear),
  )
  const [result, setResult] = useState<OpeningImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: opening } = useQuery<OpeningListResponse>({
    queryKey: ['portal-opening', ccId, year],
    queryFn: () => budgetApi.get<OpeningListResponse>(
      `/actuals/opening?fiscal_year=${year}${ccId ? `&cost_center_id=${ccId}` : ''}`,
    ),
    enabled: !!ccId,
  })
  const openingItems = opening?.items ?? []
  const openingTotal = openingItems.reduce((s, r) => s + Number(r.amount), 0)

  const downloadTemplate = async () => {
    setError(null)
    try {
      const accts = await budgetApi.get<Array<{ code: string; name: string; is_active: boolean }>>('/accounts')
      const header = ['Account Code', 'Account Name', ...OPENING_MONTHS].join(',')
      const rows = accts
        .filter((a) => a.is_active)
        .map((a) => [a.code, `"${(a.name ?? '').replace(/"/g, '""')}"`, ...Array(12).fill('')].join(','))
      const csv = ['﻿' + header, ...rows].join('\n')
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `opening-balance-template-fy${year}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to build template')
    }
  }

  const onFile = async (file: File) => {
    if (!ccId) { setError('Select a cost center first'); return }
    setBusy(true); setError(null); setResult(null)
    try {
      const r = await budgetUpload<OpeningImportResult>(
        '/actuals/opening-import', file,
        { cost_center_id: ccId, fiscal_year: String(year) },
      )
      setResult(r)
      qc.invalidateQueries({ queryKey: ['portal-opening'] })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <SectionCard
      icon={Wallet}
      title="Opening Balances"
      description="Import beginning-of-year actuals per account per month (e.g. spend incurred before go-live). Opening balances count toward actual spent and consume budget. Re-importing updates existing values."
    >
      <div className="flex flex-col gap-4 max-w-3xl">
        {/* Scope selectors */}
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-neutral-600">Cost Center</span>
            <select
              value={ccId}
              onChange={(e) => { setCcId(e.target.value); setResult(null); setError(null) }}
              className="h-9 w-64 rounded-lg border border-neutral-300 bg-white px-3 text-sm"
            >
              <option value="">Select a cost center…</option>
              {activeCcs.map((cc) => (
                <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-neutral-600">Fiscal Year</span>
            <select
              value={year}
              onChange={(e) => { setYear(Number(e.target.value)); setResult(null) }}
              className="h-9 w-32 rounded-lg border border-neutral-300 bg-white px-3 text-sm"
            >
              {years.map((y) => <option key={y} value={y}>FY {y}</option>)}
            </select>
          </label>
        </div>

        {/* Actions */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={downloadTemplate}
            className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 h-9 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
          >
            <Download className="h-3.5 w-3.5" />
            Download Template
          </button>
          <button
            type="button"
            disabled={!ccId || busy}
            onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 h-9 text-xs font-medium text-white hover:bg-[#064A4A] disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {busy ? 'Importing…' : 'Import CSV'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f) }}
          />
          <span className="text-[11px] text-neutral-400">
            Columns: Account Code + Jan…Dec. Scoped to the selected cost center + year.
          </span>
        </div>

        {error && <Toast ok={false} msg={error} />}
        {result && (
          <Toast
            ok={result.errors.length === 0}
            msg={
              `${result.rows_created} created · ${result.rows_updated} updated` +
              (result.errors.length > 0 ? ` · ${result.errors.length} error(s): ${result.errors.slice(0, 3).join('; ')}` : '')
            }
          />
        )}

        {/* Current opening preview */}
        {ccId && openingItems.length > 0 && (
          <div className="rounded-lg border border-neutral-200 overflow-hidden">
            <div className="flex items-center justify-between bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              <span>{openingItems.length} opening row{openingItems.length !== 1 ? 's' : ''} for this scope</span>
              <span className="font-mono font-medium">Total: {openingTotal.toLocaleString('en-CA', { style: 'currency', currency: 'CAD' })}</span>
            </div>
            <div className="max-h-56 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white">
                  <tr className="border-b border-neutral-100 text-neutral-500">
                    <th className="px-3 py-1.5 text-left font-medium">Account</th>
                    <th className="px-3 py-1.5 text-left font-medium">Month</th>
                    <th className="px-3 py-1.5 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {openingItems.map((r) => (
                    <tr key={`${r.account_id}-${r.month}`} className="border-b border-neutral-50">
                      <td className="px-3 py-1.5">
                        <span className="font-mono text-neutral-500 mr-1">{r.account_code}</span>
                        <span className="text-neutral-700">{r.account_name}</span>
                      </td>
                      <td className="px-3 py-1.5 text-neutral-600">{OPENING_MONTHS[r.month - 1] ?? r.month}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                        {Number(r.amount).toLocaleString('en-CA', { style: 'currency', currency: 'CAD' })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </SectionCard>
  )
}

export default function BudgetConfigPage() {
  const { user, token, refreshToken } = useAuthStore()

  if (!user) return <Navigate to="/login" replace />
  // Page now hosts Cost Centers management (open to finance roles) plus the
  // existing admin-only sections (gated below at section level).
  if (!CC_MANAGE_ROLES.has(user.role)) return <Navigate to="/" replace />
  const isAdmin = user.role === 'system_admin'

  // Deep-link to Catalog page (Catalog CRUD lives in EPMS frontend).
  const session = token && user
    ? encodeSession(token, refreshToken ?? '', user)
    : ''
  const catalogHref = session
    ? `${EPMS_URL}/budget/catalog#__session=${session}`
    : `${EPMS_URL}/budget/catalog`

  return (
    <PortalChromeLayout
      activeKey="portal:/budget/config"
      title="Budget Config"
      subtitle="Cost centers, fiscal year, alert thresholds, over-budget approval policy, and decomposition limits"
    >
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-col gap-6">

          <CostCentersSection />

          {OPENING_ROLES.has(user.role) && <OpeningBalanceSection />}

          {isAdmin && (
            <>
              <CompanyConfigSection />
              <DecompositionSettingsSection />

              {/* Pointer to Catalog CRUD */}
              <section className="rounded-xl border border-dashed border-neutral-300 bg-white p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-sm font-semibold text-neutral-900">Account Catalog (L1 / L2)</h2>
                    <p className="mt-1 text-xs text-neutral-500 leading-relaxed">
                      Adding, editing, importing or exporting L1 categories and Accounts (including factor configuration)
                      is now done in the Account Catalog page. The catalog is shared across all cost centers.
                    </p>
                  </div>
                  <a
                    href={catalogHref}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 transition-colors"
                  >
                    Open Catalog
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </div>
              </section>
            </>
          )}

        </div>
      </div>
    </PortalChromeLayout>
  )
}

// ── Cost Centers section (mdm-api CRUD) ───────────────────────────────────────

interface ApiDepartment {
  id: string
  code: string
  name: string
  is_active: boolean
}

interface ApiCostCenter {
  id: string
  code: string
  name: string
  is_active: boolean
  department_id: string
  department_code?: string
  department_name?: string
}

interface CcFormData {
  code: string
  name: string
  department_id: string
  is_active: boolean
}

const BLANK_CC: CcFormData = { code: '', name: '', department_id: '', is_active: true }

function CostCentersSection() {
  const qc = useQueryClient()
  const { data: ccs = [], isLoading } = useQuery<ApiCostCenter[]>({
    queryKey: ['portal-cost-centers'],
    queryFn: () => mdmApi.get<ApiCostCenter[]>('/cost-centers'),
  })
  const { data: deptResp } = useQuery<{ items: ApiDepartment[] }>({
    queryKey: ['portal-departments'],
    queryFn: () => mdmApi.get('/departments'),
  })
  const departments = useMemo(() => deptResp?.items ?? [], [deptResp])
  const deptMap = useMemo(
    () => Object.fromEntries(departments.map((d) => [d.id, d])),
    [departments],
  )

  const [search, setSearch] = useState('')
  const [filterDeptId, setFilterDeptId] = useState('')
  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [error, setError] = useState('')

  const filtered = ccs.filter((cc) => {
    const q = search.toLowerCase()
    const matchSearch = !q || cc.code.toLowerCase().includes(q) || cc.name.toLowerCase().includes(q)
    const matchDept = !filterDeptId || cc.department_id === filterDeptId
    return matchSearch && matchDept
  })
  const editingCc = typeof mode === 'object' ? ccs.find((c) => c.id === mode.edit) : null
  const usedCodes = (excludeId?: string) =>
    ccs.filter((c) => c.id !== excludeId).map((c) => c.code.toUpperCase())

  const createMut = useMutation({
    mutationFn: (body: CcFormData) =>
      mdmApi.post<ApiCostCenter>('/cost-centers', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-cost-centers'] }),
    onError: (e: any) => setError(e.message),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<CcFormData> }) =>
      mdmApi.patch<ApiCostCenter>(`/cost-centers/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-cost-centers'] }),
    onError: (e: any) => setError(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (id: string) => mdmApi.delete(`/cost-centers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-cost-centers'] }),
    onError: (e: any) => setError(e.message),
  })

  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          <Layers className="h-4 w-4" />
        </div>
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-neutral-900">Cost Centers</h2>
          <p className="mt-0.5 text-xs text-neutral-500 leading-relaxed">
            Cost Centers are sub-units within a Department. Each Cost Center has its own budget table.
            Budget accounts imported via CSV are linked to a Cost Center using its code.
            Managed by System Admin, Finance Manager, and AP Clerk.
          </p>
        </div>
      </div>

      {error && <div className="mb-3"><Toast ok={false} msg={error} /></div>}

      <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 flex-wrap">
          <div className="relative min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <input
              placeholder="Search cost centers…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-10 w-full rounded-lg border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]"
            />
          </div>
          <select
            value={filterDeptId}
            onChange={(e) => setFilterDeptId(e.target.value)}
            className="h-10 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]"
          >
            <option value="">All Departments</option>
            {departments.filter((d) => d.is_active).map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => { setError(''); setMode('add') }}
          disabled={mode !== 'none'}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />Add Cost Center
        </button>
      </div>

      {mode === 'add' && (
        <CcForm
          title="Add New Cost Center"
          initial={BLANK_CC}
          departments={departments.filter((d) => d.is_active)}
          existingCodes={usedCodes()}
          onSave={(d) => { createMut.mutate(d); setMode('none') }}
          onCancel={() => setMode('none')}
        />
      )}
      {editingCc && (
        <CcForm
          title={`Edit — ${editingCc.name}`}
          initial={{
            code: editingCc.code,
            name: editingCc.name,
            department_id: editingCc.department_id,
            is_active: editingCc.is_active,
          }}
          departments={departments.filter((d) => d.is_active)}
          existingCodes={usedCodes(editingCc.id)}
          onSave={(d) => { updateMut.mutate({ id: editingCc.id, body: d }); setMode('none') }}
          onCancel={() => setMode('none')}
        />
      )}

      <div className="rounded-lg border border-neutral-200 overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading cost centers…</div>
        ) : !filtered.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No cost centers found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                {['Cost Center', 'Code', 'Department', 'Status', ''].map((h, i) => (
                  <th
                    key={i}
                    className={cn(
                      'px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500',
                      i === 3 && 'w-24', i === 4 && 'w-24',
                    )}
                  >{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((cc, i) => {
                const dept = deptMap[cc.department_id]
                return (
                  <tr key={cc.id} className={cn('border-b border-neutral-100 last:border-b-0', i % 2 === 1 ? 'bg-neutral-50' : 'bg-white')}>
                    <td className="px-4 py-2.5 font-medium text-neutral-800">
                      <div className="flex items-center gap-2.5">
                        <Building2 className="h-4 w-4 text-primary-600 shrink-0" />
                        {cc.name}
                      </div>
                    </td>
                    <td className="px-4 py-2.5"><span className="inline-flex items-center rounded-md bg-neutral-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-neutral-700">{cc.code}</span></td>
                    <td className="px-4 py-2.5 text-xs text-neutral-600">{dept ? dept.name : cc.department_name || <span className="text-neutral-300">—</span>}</td>
                    <td className="px-4 py-2.5">
                      <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium', cc.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                        {cc.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        {deleteConfirm === cc.id ? (
                          <>
                            <button onClick={() => { deleteMut.mutate(cc.id); setDeleteConfirm(null) }} className="flex h-7 w-7 items-center justify-center rounded text-red-600 hover:bg-red-50"><Check className="h-3.5 w-3.5" /></button>
                            <button onClick={() => setDeleteConfirm(null)} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"><X className="h-3.5 w-3.5" /></button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => { setError(''); setMode({ edit: cc.id }); setDeleteConfirm(null) }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"><Pencil className="h-3.5 w-3.5" /></button>
                            <button onClick={() => { setDeleteConfirm(cc.id); setMode('none') }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-red-50 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-2 text-[11px] text-neutral-500">
          {filtered.length} of {ccs.length} cost center{ccs.length !== 1 ? 's' : ''}
        </div>
      </div>
    </section>
  )
}

function CcForm({
  initial, departments, existingCodes, onSave, onCancel, title,
}: {
  initial: CcFormData
  departments: ApiDepartment[]
  existingCodes: string[]
  onSave: (d: CcFormData) => void
  onCancel: () => void
  title: string
}) {
  const [form, setForm] = useState<CcFormData>(initial)
  const [errors, setErrors] = useState<Partial<Record<keyof CcFormData, string>>>({})
  const set = (k: keyof CcFormData, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }))

  const validate = () => {
    const e: typeof errors = {}
    if (!form.code.trim()) e.code = 'Required'
    else if (existingCodes.includes(form.code.trim().toUpperCase())) e.code = 'Code already in use'
    if (!form.name.trim()) e.name = 'Required'
    if (!form.department_id) e.department_id = 'Required'
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
          <label className="text-xs font-medium text-neutral-700">Code <span className="text-red-600">*</span></label>
          <input value={form.code} onChange={(e) => set('code', e.target.value)} placeholder="e.g. CC-MKT-03" className={fldCls(errors.code)} />
          {errors.code && <p className="text-xs text-red-600">{errors.code}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Name <span className="text-red-600">*</span></label>
          <input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="e.g. Brand & Campaigns" className={fldCls(errors.name)} />
          {errors.name && <p className="text-xs text-red-600">{errors.name}</p>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Department <span className="text-red-600">*</span></label>
          <select value={form.department_id} onChange={(e) => set('department_id', e.target.value)} className={fldCls(errors.department_id)}>
            <option value="">Select department…</option>
            {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          {errors.department_id && <p className="text-xs text-red-600">{errors.department_id}</p>}
        </div>
        <div className="mt-5 flex items-center gap-2">
          <input
            type="checkbox"
            id="cc-active"
            checked={form.is_active}
            onChange={(e) => set('is_active', e.target.checked)}
            className="h-4 w-4 rounded border-neutral-300"
          />
          <label htmlFor="cc-active" className="text-sm text-neutral-700">Active</label>
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
        <button
          onClick={() => { if (validate()) onSave({ ...form, code: form.code.trim().toUpperCase() }) }}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#064A4A]"
        >
          <Check className="h-3.5 w-3.5" />Save
        </button>
      </div>
    </div>
  )
}
