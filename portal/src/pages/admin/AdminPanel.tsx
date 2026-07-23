import { useState, useRef } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Building2, ShieldCheck, Users, CreditCard, Bell, Landmark,
  Plus, Pencil, Trash2, X, Check, Eye, EyeOff, Search,
  CheckCircle2, AlertCircle, Loader2, ArrowLeft,
  Download, Upload, ChevronLeft, ChevronRight, FileText,
  Workflow, ChevronDown, ChevronUp, Database, Ruler, Mail,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi, epmsDownload, epmsUpload, mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { UnitsOfMeasure } from './UnitsOfMeasure'

// ── Types ────────────────────────────────────────────────────────────────────

interface WorkflowNodeDef {
  id: string
  label: string
  role: string
}

type ActionKey = 'pr' | 'po' | 'pa' | 'pa_dir' | 'exp' | 'mil' | 'trv' | 'cfm' | 'budget_plan' | 'vms_visit'

interface CompanyConfig {
  name: string
  tagline: string
  module_taglines: Record<string, string>
  delivery_address: string
  logo_data_url: string | null
  logo_file_name: string | null
  default_currency: string
  enabled_currencies: string[]
  custom_currencies: { value: string; label: string; symbol: string }[]
  mfa_enabled: boolean
  password_expiry_days: number | null
  smtp_host: string | null
  smtp_port: number | null
  smtp_user: string | null
  smtp_password: string | null
  smtp_use_tls: boolean | null
  smtp_from: string | null
  notification_settings: {
    default_channel: string
    teams_webhook_url: string | null
    followup_time: string
  }
  workflow_defs?: Record<string, WorkflowNodeDef[]>
  // Sender identity for remittance advice (Finance → Payments). JSONB blob,
  // defaults to {} server-side; server params (host/port/TLS) are NOT here —
  // sending reuses the PO / internal SMTP profile.
  remittance_config?: {
    enabled?: boolean
    from_email?: string
    from_name?: string
    cc_email?: string
    smtp_user?: string
    smtp_password?: string
  } | null
}

interface ApiDepartment { id: string; name: string; code: string; is_active: boolean }
interface ApiUser {
  id: string; email: string; full_name: string; role: string
  department_id: string | null; department_name: string | null
  supervisor_id: string | null
  is_active: boolean; mfa_enabled: boolean; teams_account: string | null
  erp_person_code: string | null; erp_imported: boolean
}

const ALL_ROLES = [
  'requester','dept_manager','gm','opm','procurement_officer','procurement_manager',
  'warehouse_staff','ap_clerk','finance_manager','finance_bp','cfo','auditor',
  'vendor_manager','system_admin',
]
const ROLE_LABELS: Record<string,string> = {
  requester:'Requester', dept_manager:'Dept Manager', gm:'GM', opm:'OPM',
  procurement_officer:'Procurement Officer', procurement_manager:'Procurement Manager',
  warehouse_staff:'Warehouse Staff', ap_clerk:'AP Clerk', finance_manager:'Finance Manager',
  finance_bp:'Finance BP', cfo:'CFO', auditor:'Auditor', vendor_manager:'Vendor Manager',
  system_admin:'System Admin',
}

// ── Shared UI helpers ─────────────────────────────────────────────────────────

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-neutral-600">{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-neutral-400">{hint}</p>}
    </div>
  )
}
function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200', className)} {...props} />
}
function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none', className)} {...props} />
}
function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" onClick={() => onChange(!checked)}
      className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', checked ? 'bg-primary-600' : 'bg-neutral-200')}>
      <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4.5' : 'translate-x-0.5')} />
    </button>
  )
}
function SaveButton({ loading }: { loading: boolean }) {
  return (
    <button type="submit" disabled={loading}
      className="flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60 transition-colors">
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
      {loading ? 'Saving…' : 'Save Changes'}
    </button>
  )
}
function Toast({ ok, msg }: { ok: boolean; msg: string }) {
  return (
    <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-sm', ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
      {ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
      {msg}
    </div>
  )
}
function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-neutral-100 pb-4">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{description}</p>
    </div>
  )
}

function useConfig() {
  return useQuery<CompanyConfig>({
    queryKey: ['portal-config'],
    queryFn: () => epmsApi.get<CompanyConfig>('/config'),
  })
}
function useSaveConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<CompanyConfig>) => epmsApi.patch<CompanyConfig>('/config', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-config'] }),
  })
}

// ── 1. Company Settings ───────────────────────────────────────────────────────

// Modules that have their own tagline. Add a row here when a new module ships;
// the module's own app fetches `/config/public/branding?module=<key>`.
// 'portal' is backed by the existing top-level `tagline` field.
const MODULE_TAGLINE_KEYS: { key: string; label: string }[] = [
  { key: 'portal', label: 'Portal' },
  { key: 'epms', label: 'EPMS' },
  { key: 'oa', label: 'OA' },
  { key: 'vms', label: 'VMS' },
  { key: 'finance', label: 'Finance' },
  { key: 'booking', label: 'Booking' },
]

function CompanySettings() {
  const { data: cfg, isLoading } = useConfig()
  const save = useSaveConfig()
  const [form, setForm] = useState({ name: '', tagline: '', delivery_address: '' })
  const [logoDataUrl, setLogoDataUrl] = useState<string | null | undefined>(undefined) // undefined = not touched
  const [logoFileName, setLogoFileName] = useState<string | null | undefined>(undefined)
  // Per-module tagline overrides for epms/oa/vms (portal uses `tagline`).
  // undefined = not touched this session; falls back to cfg.module_taglines.
  const [moduleTaglines, setModuleTaglines] = useState<Record<string, string> | undefined>(undefined)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const f = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }))

  const values = cfg ? {
    name:             form.name             || cfg.name,
    tagline:          form.tagline          || cfg.tagline,
    delivery_address: form.delivery_address || cfg.delivery_address,
  } : form

  const currentLogo     = logoDataUrl  !== undefined ? logoDataUrl  : cfg?.logo_data_url  ?? null
  const currentFileName = logoFileName !== undefined ? logoFileName : cfg?.logo_file_name ?? null

  const currentModuleTaglines = moduleTaglines ?? cfg?.module_taglines ?? {}

  const taglineFor = (key: string): string =>
    key === 'portal' ? values.tagline : (currentModuleTaglines[key] ?? '')

  const setTaglineFor = (key: string, val: string) => {
    if (key === 'portal') { setForm((p) => ({ ...p, tagline: val })); return }
    setModuleTaglines({ ...currentModuleTaglines, [key]: val })
  }

  const handleLogoChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setLogoDataUrl(reader.result as string)
      setLogoFileName(file.name)
    }
    reader.readAsDataURL(file)
  }

  const handleRemoveLogo = () => {
    setLogoDataUrl(null)
    setLogoFileName(null)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const body: any = { name: values.name, tagline: values.tagline, delivery_address: values.delivery_address }
      if (moduleTaglines !== undefined) body.module_taglines = moduleTaglines
      if (logoDataUrl !== undefined) body.logo_data_url = logoDataUrl
      if (logoFileName !== undefined) body.logo_file_name = logoFileName
      await save.mutateAsync(body)
      setToast({ ok: true, msg: 'Company settings saved.' })
    } catch (err: any) {
      setToast({ ok: false, msg: err.message })
    }
  }

  if (isLoading) return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 max-w-lg">
      <SectionHeader title="Company Settings" description="Organization name, tagline, delivery address and logo used across all modules." />

      {/* Logo */}
      <Field label="Company Logo" hint="PNG or JPG, recommended 200×60 px. Shown on login page and PDF documents.">
        <div className="flex items-center gap-4 mt-1">
          {/* Preview */}
          <div className="flex h-16 w-36 shrink-0 items-center justify-center rounded-lg border border-neutral-200 bg-neutral-50 overflow-hidden">
            {currentLogo
              ? <img src={currentLogo} alt="Logo" className="max-h-full max-w-full object-contain p-1" />
              : <span className="text-xs text-neutral-400">No logo</span>
            }
          </div>
          {/* Controls */}
          <div className="flex flex-col gap-2">
            <label className="cursor-pointer inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
              <Plus className="h-3.5 w-3.5" />
              {currentLogo ? 'Replace' : 'Upload'} Logo
              <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={handleLogoChange} />
            </label>
            {currentLogo && (
              <button type="button" onClick={handleRemoveLogo}
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors">
                <X className="h-3.5 w-3.5" />Remove
              </button>
            )}
            {currentFileName && (
              <span className="text-[11px] text-neutral-400 truncate max-w-[140px]">{currentFileName}</span>
            )}
          </div>
        </div>
      </Field>

      <Field label="Company Name"><Input value={values.name} onChange={f('name')} required /></Field>

      {/* Per-module taglines — shared logo + name, but each module shows its own tagline */}
      <div className="flex flex-col gap-3">
        <p className="text-sm font-semibold text-neutral-700">Module Taglines</p>
        <p className="text-xs text-neutral-400 -mt-2">
          Each module shows the shared logo and company name, with its own tagline below.
          Leave a module blank to reuse the Portal tagline.
        </p>
        {MODULE_TAGLINE_KEYS.map((m) => (
          <Field key={m.key} label={`${m.label} Tagline`}>
            <Input value={taglineFor(m.key)} onChange={(e) => setTaglineFor(m.key, e.target.value)} />
          </Field>
        ))}
      </div>
      <Field label="Delivery Address" hint="Default delivery address on purchase orders.">
        <Textarea rows={3} value={values.delivery_address} onChange={f('delivery_address')} />
      </Field>
      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── 2. Security Settings ──────────────────────────────────────────────────────

function SecuritySettings() {
  const { data: cfg, isLoading } = useConfig()
  const save = useSaveConfig()
  const [mfa, setMfa] = useState<boolean | null>(null)
  const [expiry, setExpiry] = useState<string>('')
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const mfaVal = mfa ?? cfg?.mfa_enabled ?? false
  const expiryVal = expiry !== '' ? expiry : (cfg?.password_expiry_days?.toString() ?? '')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await save.mutateAsync({
        mfa_enabled: mfaVal,
        password_expiry_days: expiryVal ? parseInt(expiryVal) : null,
      })
      setToast({ ok: true, msg: 'Security settings saved.' })
    } catch (err: any) {
      setToast({ ok: false, msg: err.message })
    }
  }

  if (isLoading) return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-lg">
      <SectionHeader title="Security" description="MFA and password policy for all users. Email (SMTP) configuration moved to Notification Settings." />

      {/* MFA */}
      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-neutral-800">Multi-Factor Authentication</p>
            <p className="text-xs text-neutral-500 mt-0.5">Require TOTP for all user accounts.</p>
          </div>
          <Toggle checked={mfaVal} onChange={setMfa} />
        </div>
      </div>

      <Field label="Password Expiry (days)" hint="Leave blank to disable expiry.">
        <Input type="number" min="1" max="365" value={expiryVal}
          onChange={(e) => setExpiry(e.target.value)} placeholder="90" />
      </Field>

      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── 3. Department Management ──────────────────────────────────────────────────

function DepartmentManagement() {
  // Source of truth lives in mdm-api (master-data service). The shared Postgres
  // DB means epms-api / budget-api still read the same physical rows.
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<{ items: ApiDepartment[]; total: number }>({
    queryKey: ['portal-departments'],
    queryFn: () => mdmApi.get<{ items: ApiDepartment[]; total: number }>('/departments'),
  })
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; dept?: ApiDepartment } | null>(null)
  const [form, setForm] = useState({ name: '', code: '', is_active: true })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const openCreate = () => { setForm({ name: '', code: '', is_active: true }); setModal({ mode: 'create' }) }
  const openEdit = (d: ApiDepartment) => { setForm({ name: d.name, code: d.code, is_active: d.is_active }); setModal({ mode: 'edit', dept: d }) }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSaving(true)
    try {
      if (modal?.mode === 'create') {
        await mdmApi.post('/departments', form)
      } else {
        await mdmApi.patch(`/departments/${modal?.dept?.id}`, form)
      }
      qc.invalidateQueries({ queryKey: ['portal-departments'] })
      setModal(null)
    } catch (err: any) { setError(err.message) } finally { setSaving(false) }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this department?')) return
    try {
      await mdmApi.delete(`/departments/${id}`)
      qc.invalidateQueries({ queryKey: ['portal-departments'] })
    } catch (err: any) { alert(err.message) }
  }

  return (
    <div>
      <SectionHeader title="Department Management" description="Departments are shared across EPMS, OA, and all future modules." />
      <div className="mb-4 flex justify-end">
        <button onClick={openCreate}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A]">
          <Plus className="h-4 w-4" />Add Department
        </button>
      </div>
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No departments yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Code','Name','Status',''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((d, i) => (
                <tr key={d.id} className={cn('border-b border-neutral-100', i === data.items.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-600">{d.code}</td>
                  <td className="px-4 py-3 font-medium text-neutral-800">{d.name}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', d.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {d.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(d)} className="rounded p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => handleDelete(d.id)} className="rounded p-1.5 text-neutral-400 hover:bg-red-50 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
              <h3 className="font-semibold text-neutral-900">{modal.mode === 'create' ? 'Add Department' : 'Edit Department'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <Field label="Code"><Input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value.toUpperCase() }))} placeholder="DEPT-001" required /></Field>
              <Field label="Name"><Input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} placeholder="Department name" required /></Field>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Active</span>
                <Toggle checked={form.is_active} onChange={(v) => setForm((p) => ({ ...p, is_active: v }))} />
              </div>
              {error && <Toast ok={false} msg={error} />}
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Cancel</button>
                <SaveButton loading={saving} />
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 4. User Management ────────────────────────────────────────────────────────

const PAGE_SIZE = 20

interface ImportResult { created: number; updated: number; errors: string[] }

function UserManagement() {
  const qc = useQueryClient()

  // ── Filters + pagination ──
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [page, setPage] = useState(1)

  const qs = new URLSearchParams()
  qs.set('page', String(page))
  qs.set('page_size', String(PAGE_SIZE))
  if (search) qs.set('search', search)
  if (roleFilter) qs.set('role', roleFilter)

  const { data, isLoading } = useQuery<{ items: ApiUser[]; total: number }>({
    queryKey: ['portal-users', page, search, roleFilter],
    queryFn: () => epmsApi.get<{ items: ApiUser[]; total: number }>(`/users?${qs}`),
    placeholderData: (prev) => prev,
  })
  const { data: depts } = useQuery<{ items: ApiDepartment[] }>({
    queryKey: ['portal-departments'],
    queryFn: () => mdmApi.get('/departments'),
  })
  // Candidate list for the Supervisor picker (active users, single page covers headcount).
  const { data: directory } = useQuery<{ items: { id: string; full_name: string; department_id: string | null; department_name: string | null }[] }>({
    queryKey: ['portal-user-directory'],
    queryFn: () => epmsApi.get('/users/directory?page_size=100'),
  })

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, total)

  // Reset to page 1 when filters change
  const handleSearch = (v: string) => { setSearch(v); setPage(1) }
  const handleRole = (v: string) => { setRoleFilter(v); setPage(1) }

  // ── Edit modal ──
  const [showErpImport, setShowErpImport] = useState(false)
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; user?: ApiUser } | null>(null)
  const [form, setForm] = useState({ email: '', full_name: '', role: 'requester', department_id: '', supervisor_id: '', password: '', is_active: true, teams_account: '', erp_person_code: '' })
  const [showPwd, setShowPwd] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const openCreate = () => {
    setForm({ email: '', full_name: '', role: 'requester', department_id: '', supervisor_id: '', password: '', is_active: true, teams_account: '', erp_person_code: '' })
    setShowPwd(false); setError(''); setModal({ mode: 'create' })
  }
  const openEdit = (u: ApiUser) => {
    setForm({ email: u.email, full_name: u.full_name, role: u.role, department_id: u.department_id ?? '', supervisor_id: u.supervisor_id ?? '', password: '', is_active: u.is_active, teams_account: u.teams_account ?? '', erp_person_code: u.erp_person_code ?? '' })
    setShowPwd(false); setError(''); setModal({ mode: 'edit', user: u })
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault(); setError(''); setSaving(true)
    try {
      const body: any = { email: form.email, full_name: form.full_name, role: form.role, department_id: form.department_id || null, supervisor_id: form.supervisor_id || null, is_active: form.is_active, teams_account: form.teams_account || null }
      if (modal?.mode === 'create') { body.password = form.password; body.erp_person_code = form.erp_person_code.trim(); await epmsApi.post('/users', body) }
      else { if (form.password) body.password = form.password; body.erp_person_code = form.erp_person_code.trim(); await epmsApi.patch(`/users/${modal?.user?.id}`, body) }
      qc.invalidateQueries({ queryKey: ['portal-users'] })
      setModal(null)
    } catch (err: any) { setError(err.message) } finally { setSaving(false) }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Deactivate this user?')) return
    try { await epmsApi.delete(`/users/${id}`); qc.invalidateQueries({ queryKey: ['portal-users'] }) }
    catch (err: any) { alert(err.message) }
  }

  // ── Export ──
  const [exporting, setExporting] = useState(false)
  const handleExport = async () => {
    setExporting(true)
    try { await epmsDownload('/users/export', 'users.csv') }
    catch (err: any) { alert(err.message) }
    finally { setExporting(false) }
  }

  // ── Import ──
  const importRef = useRef<HTMLInputElement>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importing, setImporting] = useState(false)

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setImporting(true); setImportResult(null)
    try {
      const result = await epmsUpload<ImportResult>('/users/import', file)
      setImportResult(result)
      qc.invalidateQueries({ queryKey: ['portal-users'] })
    } catch (err: any) { alert(err.message) }
    finally { setImporting(false) }
  }

  const handleDownloadTemplate = () => {
    const header = 'email,full_name,role,department_code,is_active,teams_account,password\n'
    const example = 'alice@company.com,Alice Smith,requester,IT,true,,\n'
    const blob = new Blob([header + example], { type: 'text/csv' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'users_template.csv'; a.click()
  }

  return (
    <div>
      <SectionHeader title="User Management" description="Manage all UniOps users. Roles and departments apply across every module." />

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* Search */}
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 flex-1 min-w-[180px] max-w-xs">
          <Search className="h-4 w-4 text-neutral-400 shrink-0" />
          <input className="flex-1 text-sm focus:outline-none" placeholder="Search name or email…"
            value={search} onChange={(e) => handleSearch(e.target.value)} />
          {search && <button onClick={() => handleSearch('')} className="text-neutral-300 hover:text-neutral-500"><X className="h-3.5 w-3.5" /></button>}
        </div>

        {/* Role filter */}
        <select value={roleFilter} onChange={(e) => handleRole(e.target.value)}
          className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none">
          <option value="">All roles</option>
          {ALL_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
        </select>

        <div className="flex items-center gap-2 ml-auto">
          {/* Template */}
          <button onClick={handleDownloadTemplate}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-50 transition-colors"
            title="Download CSV template">
            <FileText className="h-4 w-4" />Template
          </button>

          {/* Import */}
          <label className={cn(
            'flex cursor-pointer items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-50 transition-colors',
            importing && 'opacity-60 cursor-not-allowed',
          )}>
            {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            Import
            <input ref={importRef} type="file" accept=".csv,text/csv" className="hidden" onChange={handleImport} disabled={importing} />
          </label>

          {/* Export */}
          <button onClick={handleExport} disabled={exporting}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-60 transition-colors">
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Export
          </button>

          {/* From ERP */}
          <button onClick={() => setShowErpImport(true)}
            className="flex items-center gap-1.5 rounded-lg border border-[#085E5E] bg-white px-3 py-2 text-sm font-medium text-[#085E5E] hover:bg-[#085E5E]/5 transition-colors">
            <Database className="h-4 w-4" />From ERP
          </button>

          {/* Add */}
          <button onClick={openCreate}
            className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] transition-colors">
            <Plus className="h-4 w-4" />Add User
          </button>
        </div>
      </div>

      {/* Import result */}
      {importResult && (
        <div className={cn(
          'mb-4 rounded-lg border px-4 py-3 text-sm',
          importResult.errors.length ? 'border-amber-200 bg-amber-50' : 'border-green-200 bg-green-50',
        )}>
          <div className="flex items-center justify-between">
            <span className={importResult.errors.length ? 'text-amber-800 font-medium' : 'text-green-800 font-medium'}>
              Import complete — {importResult.created} created, {importResult.updated} updated
              {importResult.errors.length > 0 && `, ${importResult.errors.length} error(s)`}
            </span>
            <button onClick={() => setImportResult(null)} className="text-neutral-400 hover:text-neutral-600"><X className="h-4 w-4" /></button>
          </div>
          {importResult.errors.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-amber-700">
              {importResult.errors.map((e, i) => <li key={i} className="font-mono">• {e}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* Table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No users found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Name / Email', 'Role', 'Department', 'ERP Code', 'Status', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((u, i) => (
                <tr key={u.id} className={cn('border-b border-neutral-100', i === data.items.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3">
                    <p className="font-medium text-neutral-800">{u.full_name}</p>
                    <p className="text-[11px] text-neutral-400">{u.email}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-primary-50 px-1.5 py-0.5 text-[11px] font-medium text-primary-700">
                      {ROLE_LABELS[u.role] ?? u.role}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{u.department_name ?? '—'}</td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">{u.erp_person_code ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', u.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(u)} className="rounded p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => handleDelete(u.id)} className="rounded p-1.5 text-neutral-400 hover:bg-red-50 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Pagination footer */}
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">
              {total === 0 ? 'No users' : `${rangeStart}–${rangeEnd} of ${total} users`}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40 transition-colors"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1)
                .filter((p) => p === 1 || p === totalPages || Math.abs(p - page) <= 1)
                .reduce<(number | '…')[]>((acc, p, i, arr) => {
                  if (i > 0 && p - (arr[i - 1] as number) > 1) acc.push('…')
                  acc.push(p); return acc
                }, [])
                .map((p, i) =>
                  p === '…'
                    ? <span key={`e${i}`} className="px-1 text-xs text-neutral-400">…</span>
                    : <button key={p} onClick={() => setPage(p as number)}
                        className={cn('h-7 min-w-[28px] rounded border px-2 text-xs transition-colors',
                          page === p ? 'border-primary-500 bg-primary-50 text-primary-700 font-medium' : 'border-neutral-200 text-neutral-600 hover:bg-neutral-50')}>
                        {p}
                      </button>
                )}
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40 transition-colors"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* From-ERP import drawer */}
      {showErpImport && <ErpUserImportDrawer onClose={() => setShowErpImport(false)} />}

      {/* Edit / Create modal */}
      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl overflow-y-auto max-h-[90vh]">
            <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
              <h3 className="font-semibold text-neutral-900">{modal.mode === 'create' ? 'Add User' : 'Edit User'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Full Name"><Input value={form.full_name} onChange={(e) => setForm((p) => ({ ...p, full_name: e.target.value }))} required /></Field>
                <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))} required /></Field>
                <Field label="Role">
                  <select value={form.role} onChange={(e) => setForm((p) => ({ ...p, role: e.target.value }))}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                    {ALL_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                  </select>
                </Field>
                <Field label="Department">
                  <select value={form.department_id} onChange={(e) => setForm((p) => ({ ...p, department_id: e.target.value, supervisor_id: '' }))}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                    <option value="">— None —</option>
                    {depts?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                </Field>
                <Field label="Supervisor">
                  <select value={form.supervisor_id} onChange={(e) => setForm((p) => ({ ...p, supervisor_id: e.target.value }))}
                    disabled={!form.department_id}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 disabled:bg-neutral-50 disabled:text-neutral-400">
                    <option value="">{form.department_id ? '— None —' : '— Select a department first —'}</option>
                    {directory?.items
                      .filter((u) => u.id !== modal.user?.id && !!form.department_id && u.department_id === form.department_id)
                      .map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                  </select>
                </Field>
                <Field label={modal.mode === 'create' ? 'Password *' : 'New Password (leave blank to keep)'}>
                  <div className="relative">
                    <Input type={showPwd ? 'text' : 'password'} value={form.password}
                      onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
                      required={modal.mode === 'create'} />
                    <button type="button" onClick={() => setShowPwd((v) => !v)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                      {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </Field>
                <Field label="Teams Account">
                  <Input value={form.teams_account} onChange={(e) => setForm((p) => ({ ...p, teams_account: e.target.value }))} placeholder="user@teams.example.com" />
                </Field>
                <Field label={modal.mode === 'create' ? 'ERP Code *' : 'ERP Code'}>
                  <Input value={form.erp_person_code}
                    onChange={(e) => setForm((p) => ({ ...p, erp_person_code: e.target.value }))}
                    required={modal.mode === 'create'}
                    placeholder="e.g. EMP00123" />
                </Field>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Active</span>
                <Toggle checked={form.is_active} onChange={(v) => setForm((p) => ({ ...p, is_active: v }))} />
              </div>
              {error && <Toast ok={false} msg={error} />}
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Cancel</button>
                <SaveButton loading={saving} />
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 5. Currency Settings ──────────────────────────────────────────────────────

const PRESET_CURRENCIES = ['CAD','USD','EUR','GBP','CNY','JPY','AUD','CHF']

function CurrencySettings() {
  const { data: cfg, isLoading } = useConfig()
  const save = useSaveConfig()
  const [defaultCurrency, setDefaultCurrency] = useState('')
  const [enabled, setEnabled] = useState<string[] | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const currDefault = defaultCurrency || cfg?.default_currency || 'CAD'
  const currEnabled = enabled ?? cfg?.enabled_currencies ?? ['CAD']

  const toggleEnabled = (code: string) => {
    const next = currEnabled.includes(code) ? currEnabled.filter((c) => c !== code) : [...currEnabled, code]
    setEnabled(next)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await save.mutateAsync({ default_currency: currDefault, enabled_currencies: currEnabled })
      setToast({ ok: true, msg: 'Currency settings saved.' })
    } catch (err: any) { setToast({ ok: false, msg: err.message }) }
  }

  if (isLoading) return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 max-w-lg">
      <SectionHeader title="Currency Settings" description="Default currency and enabled currencies apply to all financial modules." />
      <Field label="Default Currency">
        <select value={currDefault} onChange={(e) => setDefaultCurrency(e.target.value)}
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
          {PRESET_CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>
      <Field label="Enabled Currencies" hint="Only enabled currencies appear in transaction forms.">
        <div className="grid grid-cols-4 gap-2 mt-1">
          {PRESET_CURRENCIES.map((c) => {
            const on = currEnabled.includes(c)
            return (
              <button key={c} type="button" onClick={() => toggleEnabled(c)}
                className={cn('rounded-lg border px-3 py-2 text-sm font-mono font-medium transition-colors', on ? 'border-primary-400 bg-primary-50 text-primary-700' : 'border-neutral-200 text-neutral-500 hover:bg-neutral-50')}>
                {c}
              </button>
            )
          })}
        </div>
      </Field>
      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── 6. Notification Settings ──────────────────────────────────────────────────

function NotificationSettings() {
  const { data: cfg, isLoading } = useConfig()
  const save = useSaveConfig()
  const [channel, setChannel] = useState('')
  const [webhook, setWebhook] = useState('')
  const [followup, setFollowup] = useState('')
  const [smtp, setSmtp] = useState({ host: '', port: '', user: '', password: '', from: '', use_tls: true })
  const [showPwd, setShowPwd] = useState(false)
  const [testEmail, setTestEmail] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const ns = cfg?.notification_settings
  const channelVal = channel || ns?.default_channel || 'email_only'
  const webhookVal = webhook !== '' ? webhook : (ns?.teams_webhook_url ?? '')
  const followupVal = followup || ns?.followup_time || '09:00'
  const smtpVal = {
    host:     smtp.host     || cfg?.smtp_host || '',
    port:     smtp.port     || cfg?.smtp_port?.toString() || '',
    user:     smtp.user     || cfg?.smtp_user || '',
    password: smtp.password || cfg?.smtp_password || '',
    from:     smtp.from     || cfg?.smtp_from || '',
    use_tls:  smtp.use_tls  ?? cfg?.smtp_use_tls ?? true,
  }

  const CHANNELS = [
    { value: 'email_only', label: 'Email only' },
    { value: 'teams_only', label: 'Teams only' },
    { value: 'both',       label: 'Email + Teams' },
    { value: 'none',       label: 'Disabled' },
  ]

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await save.mutateAsync({
        notification_settings: {
          default_channel: channelVal as any,
          teams_webhook_url: webhookVal || null,
          followup_time: followupVal,
        },
        smtp_host: smtpVal.host || null,
        smtp_port: smtpVal.port ? parseInt(smtpVal.port) : null,
        smtp_user: smtpVal.user || null,
        smtp_password: smtpVal.password || null,
        smtp_from: smtpVal.from || null,
        smtp_use_tls: smtpVal.use_tls,
      })
      setToast({ ok: true, msg: 'Notification settings saved.' })
    } catch (err: any) { setToast({ ok: false, msg: err.message }) }
  }

  const handleTest = async () => {
    if (!testEmail) return
    setTesting(true); setTestResult(null)
    try {
      // kind="task" matches the SMTP profile shown on this page (smtp_*).
      // The PO-to-vendor profile is tested from EPMS Admin → Email Settings.
      await epmsApi.post('/config/test-smtp', { to: testEmail, kind: 'task' })
      setTestResult({ ok: true, msg: `Test notification sent to ${testEmail}` })
    } catch (err: any) { setTestResult({ ok: false, msg: err.message }) }
    finally { setTesting(false) }
  }

  if (isLoading) return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 max-w-lg">
      <SectionHeader title="Notification Settings" description="SMTP server and delivery preferences for internal task notifications (approvals, reminders, MFA OTP). PO emails to external vendors are configured separately in EPMS Admin → Email Settings." />

      {/* SMTP — internal task notifications */}
      <div className="rounded-lg border border-neutral-200 p-4">
        <p className="text-sm font-semibold text-neutral-700 mb-1">SMTP — Task Notifications</p>
        <p className="text-xs text-neutral-500 mb-3">Used for approval emails, daily reminders, MFA OTPs.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="SMTP Host">
            <Input value={smtpVal.host} onChange={(e) => setSmtp((p) => ({ ...p, host: e.target.value }))} placeholder="smtp.example.com" />
          </Field>
          <Field label="Port">
            <Input type="number" value={smtpVal.port} onChange={(e) => setSmtp((p) => ({ ...p, port: e.target.value }))} placeholder="587" />
          </Field>
          <Field label="Username">
            <Input value={smtpVal.user} onChange={(e) => setSmtp((p) => ({ ...p, user: e.target.value }))} placeholder="notifications@company.com" />
          </Field>
          <Field label="Password">
            <div className="relative">
              <Input type={showPwd ? 'text' : 'password'} value={smtpVal.password}
                onChange={(e) => setSmtp((p) => ({ ...p, password: e.target.value }))} />
              <button type="button" onClick={() => setShowPwd((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </Field>
          <Field label="From Address">
            <Input value={smtpVal.from} onChange={(e) => setSmtp((p) => ({ ...p, from: e.target.value }))} placeholder="noreply@company.com" />
          </Field>
          <Field label="TLS">
            <div className="flex items-center gap-2 pt-1.5">
              <Toggle checked={smtpVal.use_tls} onChange={(v) => setSmtp((p) => ({ ...p, use_tls: v }))} />
              <span className="text-xs text-neutral-500">Use TLS/STARTTLS</span>
            </div>
          </Field>
        </div>
      </div>

      <Field label="Default Notification Channel">
        <div className="grid grid-cols-2 gap-2 mt-1">
          {CHANNELS.map(({ value, label }) => (
            <button key={value} type="button" onClick={() => setChannel(value)}
              className={cn('rounded-lg border px-3 py-2.5 text-sm text-left transition-colors', channelVal === value ? 'border-primary-400 bg-primary-50 text-primary-700 font-medium' : 'border-neutral-200 text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Teams Webhook URL" hint="Required when channel is 'Teams only' or 'Email + Teams'.">
        <Input value={webhookVal} onChange={(e) => setWebhook(e.target.value)} placeholder="https://outlook.office.com/webhook/…" />
      </Field>
      <Field label="Daily Follow-up Time (UTC)" hint="Time to send pending task reminders each day.">
        <Input type="time" value={followupVal} onChange={(e) => setFollowup(e.target.value)} />
      </Field>

      {/* Test notification */}
      <div className="flex items-center gap-2">
        <Input className="flex-1" value={testEmail} onChange={(e) => setTestEmail(e.target.value)} placeholder="Send test notification to…" type="email" />
        <button type="button" onClick={handleTest} disabled={testing || !testEmail}
          className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 whitespace-nowrap">
          {testing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Test
        </button>
      </div>
      {testResult && <Toast {...testResult} />}

      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── 7. Remittance Advice ──────────────────────────────────────────────────────
//
// Sender identity for the remittance advice emailed to a vendor when a payment
// batch (or single payment) pays them — Finance → Payments. There is no mail
// server to configure here: sending reuses the PO Email SMTP profile (EPMS
// Admin → Email Settings), which itself falls back to the internal Task
// Notification SMTP profile above. remittance_config is a whole-object JSONB
// blob server-side, so the Save button is gated on the config having loaded —
// saving an empty form over a populated config would wipe it.

function RemittanceSettings() {
  const { data: cfg, isLoading } = useConfig()
  const save = useSaveConfig()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [fromEmail, setFromEmail] = useState<string | null>(null)
  const [fromName, setFromName] = useState<string | null>(null)
  const [ccEmail, setCcEmail] = useState<string | null>(null)
  const [smtpUser, setSmtpUser] = useState<string | null>(null)
  const [smtpPassword, setSmtpPassword] = useState<string | null>(null)
  const [showPwd, setShowPwd] = useState(false)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const rc = cfg?.remittance_config ?? {}
  const enabledVal = enabled ?? rc.enabled ?? false
  const fromEmailVal = fromEmail ?? rc.from_email ?? ''
  const fromNameVal = fromName ?? rc.from_name ?? ''
  const ccEmailVal = ccEmail ?? rc.cc_email ?? ''
  const smtpUserVal = smtpUser ?? rc.smtp_user ?? ''
  const smtpPasswordVal = smtpPassword ?? rc.smtp_password ?? ''

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    // Whole-object JSONB replace: always send `enabled` (the toggle the admin
    // just set), and only send optional fields that are actually filled, so an
    // untouched blank never overwrites with "".
    const body: NonNullable<CompanyConfig['remittance_config']> = { enabled: enabledVal }
    if (fromEmailVal.trim()) body.from_email = fromEmailVal.trim()
    if (fromNameVal.trim()) body.from_name = fromNameVal.trim()
    if (ccEmailVal.trim()) body.cc_email = ccEmailVal.trim()
    if (smtpUserVal.trim()) body.smtp_user = smtpUserVal.trim()
    if (smtpPasswordVal.trim()) body.smtp_password = smtpPasswordVal.trim()
    try {
      await save.mutateAsync({ remittance_config: body })
      setToast({ ok: true, msg: 'Remittance settings saved.' })
    } catch (err: any) { setToast({ ok: false, msg: err.message }) }
  }

  if (isLoading) return <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 max-w-lg">
      <SectionHeader
        title="Remittance Advice"
        description="Turns on the remittance advice email sent to a vendor when a payment pays them, and sets the sender identity it goes out under. No mail server is configured here — sending reuses the PO Email SMTP profile (EPMS Admin → Email Settings), which falls back to the internal Task Notification SMTP profile above."
      />

      <label className="flex items-center gap-2.5 cursor-pointer w-fit">
        <Toggle checked={enabledVal} onChange={setEnabled} />
        <span className="text-sm font-medium text-neutral-700">Send remittance advice emails</span>
      </label>

      <div className="grid grid-cols-2 gap-3">
        <Field label="From Email">
          <Input type="email" value={fromEmailVal} onChange={(e) => setFromEmail(e.target.value)} placeholder="remittance@company.com" />
        </Field>
        <Field label="From Name">
          <Input value={fromNameVal} onChange={(e) => setFromName(e.target.value)} placeholder="Accounts Payable" />
        </Field>
        <div className="col-span-2">
          <Field label="CC Email" hint="Optional — copied on every remittance email.">
            <Input type="email" value={ccEmailVal} onChange={(e) => setCcEmail(e.target.value)} />
          </Field>
        </div>
      </div>

      <div className="rounded-lg border border-neutral-200 p-4">
        <p className="text-sm font-semibold text-neutral-700 mb-1">SMTP Credential Override</p>
        <p className="text-xs text-neutral-500 mb-3">Optional. Leave blank to use the shared SMTP credentials — these exist only for servers that reject a From address that doesn't match the authenticated account.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="SMTP User">
            <Input value={smtpUserVal} onChange={(e) => setSmtpUser(e.target.value)} />
          </Field>
          <Field label="SMTP Password">
            <div className="relative">
              <Input type={showPwd ? 'text' : 'password'} value={smtpPasswordVal}
                onChange={(e) => setSmtpPassword(e.target.value)} />
              <button type="button" onClick={() => setShowPwd((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </Field>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <SaveButton loading={save.isPending} />
        {toast && <Toast {...toast} />}
      </div>
    </form>
  )
}

// ── Approval Workflows ───────────────────────────────────────────────────────

const ACTION_KEYS: ActionKey[] = ['pr', 'po', 'pa', 'pa_dir', 'exp', 'mil', 'trv', 'cfm', 'budget_plan', 'vms_visit']

const ACTION_KEY_LABELS: Record<ActionKey, string> = {
  pr:          'Purchase Request',
  po:          'Purchase Order',
  pa:          'PA (PO-Linked)',
  pa_dir:      'PA (Direct)',
  exp:         'General Expense',
  mil:         'Mileage Claim',
  trv:         'Travel Expense',
  cfm:         'Custom Form',
  budget_plan: 'Budget Plan',
  vms_visit:   'VMS Visit',
}

const WORKFLOW_ROLES = [
  { value: 'supervisor',          label: 'Supervisor (dept-scoped, per requester)' },
  { value: 'dept_manager',        label: 'Department Manager' },
  { value: 'director',            label: 'Director (dept-scoped, optional)' },
  { value: 'gm_or_opm',          label: 'GM / OPM (auto-resolved)' },
  { value: 'gm',                  label: 'GM' },
  { value: 'opm',                 label: 'OPM' },
  { value: 'procurement_manager', label: 'Procurement Manager' },
  { value: 'finance_manager',     label: 'Finance Manager' },
  { value: 'finance_bp',          label: 'Finance BP' },
  { value: 'ap_clerk',            label: 'AP Clerk' },
  // VMS-local role — vms-api resolves the specific user from
  // `vms_config.quality_manager_user_ids` (see S2_ARCHITECTURE_REVIEW.md F2).
  // Only meaningful on the vms_visit workflow.
  { value: 'quality_manager',     label: 'Quality Manager (VMS-local)' },
]

const WORKFLOW_DEFAULTS: Record<ActionKey, WorkflowNodeDef[]> = {
  pr:     [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'gm_or_opm', role: 'gm_or_opm', label: 'GM / OPM' }],
  po:     [{ id: 'proc_mgr', role: 'procurement_manager', label: 'Procurement Manager' }, { id: 'gm_or_opm', role: 'gm_or_opm', label: 'GM / OPM' }],
  pa:     [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'gm_or_opm', role: 'gm_or_opm', label: 'GM / OPM' }, { id: 'finance_bp', role: 'finance_bp', label: 'Finance BP' }, { id: 'finance_mgr', role: 'finance_manager', label: 'Finance Manager' }],
  pa_dir: [{ id: 'finance_bp', role: 'finance_bp', label: 'Finance BP' }, { id: 'finance_mgr', role: 'finance_manager', label: 'Finance Manager' }],
  exp:    [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'finance_bp', role: 'finance_bp', label: 'Finance BP' }],
  mil:    [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'finance_bp', role: 'finance_bp', label: 'Finance BP' }],
  trv:    [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'finance_bp', role: 'finance_bp', label: 'Finance BP' }],
  cfm:    [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }],
  budget_plan: [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }, { id: 'finance_mgr', role: 'finance_manager', label: 'Finance Manager' }],
  // VMS visit: default single-step. Admin adds a `quality_manager` row for
  // GMP / Lab dual approval (S2_ARCHITECTURE_REVIEW.md §"locked decisions").
  vms_visit: [{ id: 'dept_manager', role: 'dept_manager', label: 'Department Manager' }],
}

function ApprovalWorkflows() {
  const { data: cfg } = useConfig()
  const qc = useQueryClient()
  const [activeKey, setActiveKey] = useState<ActionKey>('pr')
  const [defs, setDefs] = useState<Record<string, WorkflowNodeDef[]>>({})
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  // Initialise local state from fetched config (fill gaps with defaults)
  const [initialised, setInitialised] = useState(false)
  if (cfg && !initialised) {
    const merged: Record<string, WorkflowNodeDef[]> = {}
    for (const key of ACTION_KEYS) {
      merged[key] = cfg.workflow_defs?.[key] ?? WORKFLOW_DEFAULTS[key] ?? []
    }
    setDefs(merged)
    setInitialised(true)
  }

  const steps = defs[activeKey] ?? []

  const setSteps = (next: WorkflowNodeDef[]) =>
    setDefs(prev => ({ ...prev, [activeKey]: next }))

  const moveUp = (i: number) => {
    const updated = [...steps]
    ;[updated[i - 1], updated[i]] = [updated[i], updated[i - 1]]
    setSteps(updated)
  }

  const moveDown = (i: number) => {
    const updated = [...steps]
    ;[updated[i], updated[i + 1]] = [updated[i + 1], updated[i]]
    setSteps(updated)
  }

  const removeStep = (i: number) =>
    setSteps(steps.filter((_, idx) => idx !== i))

  const addStep = () =>
    setSteps([...steps, { id: crypto.randomUUID(), label: 'New Step', role: 'dept_manager' }])

  const updateStep = (i: number, patch: Partial<WorkflowNodeDef>) =>
    setSteps(steps.map((s, idx) => idx === i ? { ...s, ...patch } : s))

  const handleSave = async () => {
    if (saving) return
    setSaving(true)
    try {
      await epmsApi.patch('/config', { workflow_defs: defs })
      qc.invalidateQueries({ queryKey: ['portal-config'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-base font-semibold text-neutral-900">Approval Workflows</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Configure the approval steps for each document type across all UniOps modules.
          Changes take effect on the next submission — in-flight documents are unaffected.
        </p>
      </div>

      {/* Action key tabs */}
      <div className="flex flex-wrap gap-1 rounded-xl bg-neutral-100 p-1">
        {ACTION_KEYS.map(key => (
          <button
            key={key}
            onClick={() => setActiveKey(key)}
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
              activeKey === key
                ? 'bg-white text-neutral-900 shadow-sm'
                : 'text-neutral-500 hover:text-neutral-700',
            )}
          >
            {key === 'pa_dir'      ? 'PA-DIR'
              : key === 'vms_visit' ? 'VMS Visit'
              : key.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Step list */}
      <div className="flex flex-col gap-2">
        <p className="text-xs text-neutral-400 font-medium uppercase tracking-wide">
          {ACTION_KEY_LABELS[activeKey]} — approval chain
        </p>

        {steps.length === 0 && (
          <div className="rounded-lg border border-dashed border-neutral-200 py-6 text-center text-sm text-neutral-400">
            No steps configured. Add at least one step.
          </div>
        )}

        {steps.map((step, i) => (
          <div key={step.id} className="flex items-center gap-2 rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2.5">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#085E5E]/10 text-xs font-bold text-[#085E5E]">
              {i + 1}
            </span>

            <input
              value={step.label}
              onChange={e => updateStep(i, { label: e.target.value })}
              placeholder="Step label…"
              className="flex-1 min-w-0 rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:border-[#085E5E]"
            />

            <select
              value={step.role}
              onChange={e => updateStep(i, { role: e.target.value })}
              className="h-8 rounded-lg border border-neutral-200 bg-white px-2 text-xs text-neutral-800 focus:outline-none focus:border-[#085E5E]"
            >
              {WORKFLOW_ROLES.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>

            <div className="flex items-center gap-0.5">
              <button onClick={() => moveUp(i)} disabled={i === 0}
                className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-200 disabled:opacity-30">
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <button onClick={() => moveDown(i)} disabled={i === steps.length - 1}
                className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-200 disabled:opacity-30">
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
              <button onClick={() => removeStep(i)} disabled={steps.length <= 1}
                className="flex h-7 w-7 items-center justify-center rounded text-neutral-300 hover:bg-red-50 hover:text-red-500 disabled:opacity-30">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}

        <button onClick={addStep}
          className="flex items-center gap-1.5 self-start text-sm font-medium text-[#085E5E] hover:text-[#064A4A] mt-1">
          <Plus className="h-4 w-4" /> Add step
        </button>
      </div>

      {/* Save */}
      <div className="flex items-center gap-3 border-t border-neutral-100 pt-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 transition-colors"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Save Workflow
        </button>
        {saved && (
          <span className="flex items-center gap-1.5 text-sm text-green-600">
            <CheckCircle2 className="h-4 w-4" /> Saved
          </span>
        )}
      </div>

      <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-600">
        <strong>Note:</strong> Changes apply to new submissions only.
        Documents already in-flight continue on the workflow active at time of submission.
      </div>
    </div>
  )
}

// ── ERP MDM ───────────────────────────────────────────────────────────────────

type ErpKind = 'material' | 'supplier' | 'person'

interface ErpSyncState {
  kind: string
  last_ts: string | null
  last_synced_at: string | null
  last_status: string | null
  last_message: string | null
  last_row_count: number
}

interface ErpSyncResult {
  kind: string; mode: string; total: number; inserted: number; updated: number
  last_ts: string; status: string; message: string
}

function ErpMdmSection() {
  const [tab, setTab] = useState<ErpKind>('material')
  return (
    <div>
      <SectionHeader title="ERP MDM" description="Mirror of ERP master data. Sync manually, then import into Users/Vendors or use in PR Type 1." />
      <div className="mb-4 flex gap-1 border-b border-neutral-200">
        {(['material','supplier','person'] as ErpKind[]).map(k => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              tab === k ? 'border-[#085E5E] text-[#085E5E]' : 'border-transparent text-neutral-500 hover:text-neutral-700')}>
            {k === 'material' ? 'Materials' : k === 'supplier' ? 'Suppliers' : 'Persons'}
          </button>
        ))}
      </div>
      {tab === 'material' && <ErpMaterialsTab />}
      {tab === 'supplier' && <ErpSuppliersTab />}
      {tab === 'person'   && <ErpPersonsTab />}
    </div>
  )
}

function ErpSyncToolbar({ kind, onSearchChange, search, filters }: {
  kind: ErpKind, onSearchChange: (v: string) => void, search: string, filters?: React.ReactNode
}) {
  const qc = useQueryClient()
  const status = useQuery<Record<string, ErpSyncState | null>>({
    queryKey: ['erp-sync-status'],
    queryFn: () => mdmApi.get('/erp/sync/status'),
    refetchInterval: 10_000,
  })
  const s = status.data?.[kind]
  const [syncing, setSyncing] = useState<'inc' | 'full' | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const trigger = async (full: boolean) => {
    if (full && !confirm('Pull ALL records from ERP from the beginning. This may take several minutes. Continue?')) return
    setSyncing(full ? 'full' : 'inc')
    try {
      const r = await mdmApi.post<ErpSyncResult>(`/erp/sync/${kind}?full=${full}`)
      setToast(`Sync ok — ${r.total} records (${r.inserted} inserted, ${r.updated} updated)`)
      qc.invalidateQueries({ queryKey: ['erp-sync-status'] })
      qc.invalidateQueries({ queryKey: ['erp', kind] })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setToast(`Sync failed: ${msg}`)
    } finally {
      setSyncing(null)
      setTimeout(() => setToast(null), 6000)
    }
  }

  return (
    <div className="mb-4 space-y-3">
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        {s ? <>
          Last synced: {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : '—'}
          {' · '}<span className={cn(s.last_status === 'success' ? 'text-green-700' : s.last_status === 'failed' ? 'text-red-700' : '')}>{s.last_status || 'never'}</span>
          {' · '}{s.last_row_count} rows
        </> : 'Never synced'}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => trigger(false)} disabled={!!syncing}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60 transition-colors">
          {syncing === 'inc' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Sync Incremental
        </button>
        <button onClick={() => trigger(true)} disabled={!!syncing}
          className="flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-60 transition-colors">
          {syncing === 'full' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Full Resync
        </button>
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 ml-auto flex-1 min-w-[180px] max-w-xs">
          <Search className="h-4 w-4 text-neutral-400 shrink-0" />
          <input className="flex-1 text-sm focus:outline-none" placeholder="Search…"
            value={search} onChange={(e) => onSearchChange(e.target.value)} />
        </div>
        {filters}
      </div>
      {toast && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{toast}</div>}
    </div>
  )
}

interface ErpMaterial {
  erp_part_no: string; description: string | null; unit_meas: string | null
  dim_quality: string | null; part_status: string | null; item_mes_type: string | null
  erp_rowversion: string | null; synced_at: string
}

function ErpMaterialsTab() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'A' | 'B' | ''>('A')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  if (statusFilter) qs.set('part_status', statusFilter)
  const { data, isLoading } = useQuery<{ items: ErpMaterial[]; total: number }>({
    queryKey: ['erp', 'material', page, search, statusFilter],
    queryFn: () => mdmApi.get(`/erp/materials?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar
        kind="material"
        search={search}
        onSearchChange={(v) => { setSearch(v); setPage(1) }}
        filters={
          <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value as 'A' | 'B' | ''); setPage(1) }}
            className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none">
            <option value="A">Active (A)</option>
            <option value="B">Inactive (B)</option>
            <option value="">All</option>
          </select>
        }
      />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No materials. Click Sync Incremental to fetch from ERP.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Part No', 'Description', 'Unit', 'Spec', 'MES Type', 'Status', 'ERP rowversion'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {data.items.map(m => (
                <tr key={m.erp_part_no} className="border-b border-neutral-100">
                  <td className="px-4 py-3 font-mono text-xs">{m.erp_part_no}</td>
                  <td className="px-4 py-3 text-neutral-800">{m.description}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.unit_meas}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.dim_quality}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.item_mes_type}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium',
                      m.part_status === 'A' ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {m.part_status || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-[11px] text-neutral-400">
                    {m.erp_rowversion ? new Date(m.erp_rowversion).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">
              {(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}
            </span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

interface ErpSupplierRow {
  erp_supplier_code: string; supplier_name: string; supplier_tel: string | null
  supplier_address: string | null; supplier_type: string | null
  erp_rowversion: string | null; synced_at: string
}

function ErpSuppliersTab() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  const { data, isLoading } = useQuery<{ items: ErpSupplierRow[]; total: number }>({
    queryKey: ['erp', 'supplier', page, search],
    queryFn: () => mdmApi.get(`/erp/suppliers?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar kind="supplier" search={search} onSearchChange={(v) => { setSearch(v); setPage(1) }} />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
          : !data?.items.length ? <div className="py-10 text-center text-sm text-neutral-400">No suppliers. Click Sync.</div>
          : <table className="w-full text-sm">
              <thead className="border-b border-neutral-100 bg-neutral-50">
                <tr>{['Code', 'Name', 'Type', 'Tel', 'Address'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map(s => (
                  <tr key={s.erp_supplier_code} className="border-b border-neutral-100">
                    <td className="px-4 py-3 font-mono text-xs">{s.erp_supplier_code}</td>
                    <td className="px-4 py-3">{s.supplier_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_type}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_tel}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_address}</td>
                  </tr>
                ))}
              </tbody>
            </table>
        }
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">{(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

interface ErpPersonRow {
  erp_person_code: string; person_name: string; company_name: string | null
  department_code: string | null; department_name: string | null; is_valid: boolean
  erp_rowversion: string | null; synced_at: string
}

function ErpPersonsTab() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  const { data, isLoading } = useQuery<{ items: ErpPersonRow[]; total: number }>({
    queryKey: ['erp', 'person', page, search],
    queryFn: () => mdmApi.get(`/erp/persons?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar kind="person" search={search} onSearchChange={(v) => { setSearch(v); setPage(1) }} />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
          : !data?.items.length ? <div className="py-10 text-center text-sm text-neutral-400">No persons. Click Sync.</div>
          : <table className="w-full text-sm">
              <thead className="border-b border-neutral-100 bg-neutral-50">
                <tr>{['Code', 'Name', 'Company', 'Department', 'Valid'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map(p => (
                  <tr key={p.erp_person_code} className="border-b border-neutral-100">
                    <td className="px-4 py-3 font-mono text-xs">{p.erp_person_code}</td>
                    <td className="px-4 py-3">{p.person_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{p.company_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{p.department_name} ({p.department_code})</td>
                    <td className="px-4 py-3">
                      <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium',
                        p.is_valid ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                        {p.is_valid ? 'Yes' : 'No'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
        }
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">{(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

// ── ERP User Import Drawer ────────────────────────────────────────────────────

function ErpUserImportDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const PAGE_SIZE = 50
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [excludeImported, setExcludeImported] = useState(true)
  const [selected, setSelected] = useState<Record<string, { email: string }>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ created: { email: string; full_name: string; temp_password: string }[]; errors: { erp_person_code: string; reason: string }[] } | null>(null)

  // Fetch ALL existing users to know which ERP codes are already imported.
  // The /users endpoint caps page_size at 200, so we page through it rather
  // than asking for an over-cap size (which would 422 and silently break the
  // "only show not-imported" filter).
  const { data: existingUsers } = useQuery<ApiUser[]>({
    queryKey: ['portal-users', 'all-for-erp-import'],
    queryFn: async () => {
      const all: ApiUser[] = []
      for (let p = 1; ; p++) {
        const resp = await epmsApi.get<{ items: ApiUser[]; total: number }>(`/users?page=${p}&page_size=200`)
        all.push(...resp.items)
        if (resp.items.length === 0 || all.length >= resp.total) break
      }
      return all
    },
    enabled: excludeImported,
  })
  const importedCodes = (existingUsers || [])
    .map((u: ApiUser) => (u as unknown as { erp_person_code?: string | null }).erp_person_code)
    .filter((c): c is string => !!c)

  const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
  if (search) qs.set('search', search)
  if (excludeImported && importedCodes.length) qs.set('exclude_codes', importedCodes.join(','))

  const { data, isLoading } = useQuery<{ items: ErpPersonRow[]; total: number }>({
    queryKey: ['erp-import-persons', page, search, excludeImported, importedCodes.join(',')],
    queryFn: () => mdmApi.get(`/erp/persons?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const selectedCount = Object.keys(selected).length
  const allEmailsFilled = Object.values(selected).every(s => s.email.trim().length > 0)

  const toggle = (code: string) => {
    setSelected(prev => {
      const next = { ...prev }
      if (next[code]) delete next[code]
      else next[code] = { email: '' }
      return next
    })
  }

  const submit = async () => {
    setSubmitting(true); setError('')
    try {
      const items = Object.entries(selected).map(([code, v]) => ({
        erp_person_code: code,
        email: v.email.trim(),
        role: 'requester',
      }))
      const resp = await epmsApi.post<{ created: { email: string; full_name: string; temp_password: string }[]; errors: { erp_person_code: string; reason: string }[] }>('/users/import-from-erp', { items })
      setResult(resp)
      qc.invalidateQueries({ queryKey: ['portal-users'] })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end">
      <div className="bg-white w-full max-w-3xl h-full overflow-hidden flex flex-col">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold">Import users from ERP</h2>
          <button onClick={onClose}><X className="h-4 w-4 text-neutral-500" /></button>
        </div>

        {result ? (
          <div className="flex-1 overflow-auto p-4 space-y-3">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm">
              <strong>{result.created.length} created</strong>, {result.errors.length} errors.
            </div>
            {result.created.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-neutral-600 mb-1">Temporary passwords (copy now — not shown again):</p>
                <table className="w-full text-xs border border-neutral-200">
                  <thead><tr className="bg-neutral-50">
                    <th className="px-2 py-1 text-left">Email</th>
                    <th className="px-2 py-1 text-left">Temp password</th>
                  </tr></thead>
                  <tbody>
                    {result.created.map((c, i) => (
                      <tr key={i} className="border-t border-neutral-100">
                        <td className="px-2 py-1 font-mono">{c.email}</td>
                        <td className="px-2 py-1 font-mono">{c.temp_password}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {result.errors.length > 0 && (
              <ul className="text-xs text-amber-700 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e.erp_person_code}: {e.reason}</li>)}
              </ul>
            )}
            <button onClick={onClose} className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white">Done</button>
          </div>
        ) : (
          <>
            <div className="border-b border-neutral-200 px-4 py-3 space-y-2">
              <div className="flex items-center gap-2">
                <input className="flex-1 rounded-lg border border-neutral-200 px-3 py-2 text-sm"
                  placeholder="Search code or name…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
                <label className="flex items-center gap-1 text-xs text-neutral-600">
                  <input type="checkbox" checked={excludeImported} onChange={e => { setExcludeImported(e.target.checked); setPage(1) }} />
                  Only show not-imported
                </label>
              </div>
              <p className="text-xs text-neutral-500">Role defaults to <code>requester</code>. Department auto-maps from ERP department code. Edit later in User Management.</p>
            </div>

            <div className="flex-1 overflow-auto p-4">
              {isLoading ? <div className="text-sm text-neutral-400">Loading…</div>
                : !data?.items.length ? <div className="text-sm text-neutral-400">No persons to import.</div>
                : <table className="w-full text-sm">
                    <thead className="border-b border-neutral-200">
                      <tr>
                        <th className="px-2 py-2"></th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Code</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Name</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Dept</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Email *</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map(p => {
                        const isSel = !!selected[p.erp_person_code]
                        return (
                          <tr key={p.erp_person_code} className="border-b border-neutral-100">
                            <td className="px-2 py-2"><input type="checkbox" checked={isSel} onChange={() => toggle(p.erp_person_code)} /></td>
                            <td className="px-2 py-2 font-mono text-xs">{p.erp_person_code}</td>
                            <td className="px-2 py-2">{p.person_name}</td>
                            <td className="px-2 py-2 text-xs text-neutral-500">{p.department_name}</td>
                            <td className="px-2 py-2">
                              <input
                                disabled={!isSel}
                                placeholder="email@royalmilk.com"
                                value={selected[p.erp_person_code]?.email || ''}
                                onChange={e => setSelected(prev => ({ ...prev, [p.erp_person_code]: { email: e.target.value } }))}
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs disabled:bg-neutral-50"
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
              }
            </div>

            {total > 0 && (
              <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-2">
                <span className="text-xs text-neutral-400">{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}</span>
                <div className="flex items-center gap-1">
                  <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                    className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
                  <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                    className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}

            <div className="border-t border-neutral-200 px-4 py-3 flex items-center justify-between">
              <span className="text-xs text-neutral-500">
                {selectedCount} selected{selectedCount > 0 && !allEmailsFilled && ' · email required for each row'}
              </span>
              <div className="flex items-center gap-2">
                {error && <span className="text-xs text-red-600">{error}</span>}
                <button onClick={onClose} className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">Cancel</button>
                <button onClick={submit} disabled={!selectedCount || !allEmailsFilled || submitting}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white disabled:opacity-60 flex items-center gap-1.5">
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Import {selectedCount} user{selectedCount === 1 ? '' : 's'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Admin Panel layout ────────────────────────────────────────────────────────

const SECTIONS = [
  { key: 'company',      label: 'Company Settings',     icon: Building2 },
  { key: 'security',     label: 'Security',             icon: ShieldCheck },
  { key: 'departments',  label: 'Departments',          icon: Landmark },
  { key: 'uom',          label: 'Units of Measure',     icon: Ruler },
  { key: 'users',        label: 'User Management',      icon: Users },
  { key: 'currency',     label: 'Currency Settings',    icon: CreditCard },
  { key: 'notifications',label: 'Notification Settings',icon: Bell },
  { key: 'remittance',   label: 'Remittance Advice',    icon: Mail },
  { key: 'workflows',    label: 'Approval Workflows',   icon: Workflow },
  { key: 'erp_mdm',      label: 'ERP MDM',              icon: Database },
]

export default function AdminPanel() {
  const { user } = useAuthStore()
  const [section, setSection] = useState('company')

  if (user?.role !== 'system_admin') return <Navigate to="/" replace />

  const currentSection = SECTIONS.find((s) => s.key === section)

  return (
    <div className="flex h-screen overflow-hidden bg-[#F5F6FA]">

      {/* ── Teal sidebar — matches Portal main nav style ─────────────── */}
      <nav className="w-56 shrink-0 flex flex-col bg-[#085E5E] overflow-hidden">
        {/* Brand */}
        <div className="flex h-[60px] items-center gap-2.5 border-b border-white/10 px-4 shrink-0">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/15 text-white font-bold text-sm select-none">
            U
          </div>
          <div className="leading-tight min-w-0">
            <p className="text-sm font-bold text-white tracking-tight">UniOps</p>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-white/50">Admin Panel</p>
          </div>
        </div>

        {/* Back to Portal */}
        <div className="px-2 pt-3 pb-2 border-b border-white/10 shrink-0">
          <a
            href="/"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-white/65 hover:bg-white/10 hover:text-white transition-colors"
          >
            <ArrowLeft className="h-4 w-4 shrink-0" />
            Back to Portal
          </a>
        </div>

        {/* Section nav */}
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {SECTIONS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setSection(key)}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors text-left',
                section === key
                  ? 'bg-white/20 text-white font-medium'
                  : 'text-white/65 hover:bg-white/10 hover:text-white',
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </button>
          ))}
        </div>
      </nav>

      {/* ── Main content area ─────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">

        {/* Header */}
        <header className="flex h-[60px] shrink-0 items-center gap-3 border-b border-neutral-200 bg-white px-6">
          {currentSection && (
            <div className="flex items-center gap-2">
              <currentSection.icon className="h-4 w-4 text-neutral-400 shrink-0" />
              <h1 className="text-sm font-semibold text-neutral-800">{currentSection.label}</h1>
            </div>
          )}
        </header>

        {/* Scrollable content — no max-width cap so wide sections (User Management) expand fully */}
        <main className="flex-1 overflow-y-auto p-6">
          {section === 'company'       && <CompanySettings />}
          {section === 'security'      && <SecuritySettings />}
          {section === 'departments'   && <DepartmentManagement />}
          {section === 'uom'           && <UnitsOfMeasure />}
          {section === 'users'         && <UserManagement />}
          {section === 'currency'      && <CurrencySettings />}
          {section === 'notifications' && <NotificationSettings />}
          {section === 'remittance'    && <RemittanceSettings />}
          {section === 'workflows'     && <ApprovalWorkflows />}
          {section === 'erp_mdm'     && <ErpMdmSection />}
        </main>
      </div>
    </div>
  )
}
