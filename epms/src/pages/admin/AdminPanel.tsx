import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useQueryClient } from '@tanstack/react-query'
import { authService } from '@/services/auth'
import { configService } from '@/services/config'
import { useAuthStore } from '@/stores/auth.store'
import {
  Users, Workflow, Building2, Clock, Bell, Send, CreditCard,
  PiggyBank, PackageCheck, ShieldCheck,
  Plus, Pencil, Trash2, X, Check, Search, ImagePlus, Landmark, KeyRound, Eye, EyeOff, Mail, FileText,
  Download, Upload, ChevronDown,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { PmsImportPanel } from './PmsImportPanel'
import {
  useConfig, useUpdateConfig, useRoles,
} from '@/hooks/useConfig'
import { useDepartments } from '@/hooks/useDepartments'
import { useUsers, useCreateUser, useUpdateUser, useDeleteUser } from '@/hooks/useUsers'
import { type ApiUserRole, type ApiUser } from '@/services/users'
import {
  type CustomRole,
  type PdfTemplateSettings,
  type ServiceGrSlaConfig, type GrNotificationSlaConfig,
  type PrepaymentConfig, type CollectionConfig,
  type NotificationSettings, type EmailTemplate,
} from '@/services/config'
import { ROLE_LABELS } from '@/stores/user.store'
import { TEMPLATE_VARIABLE_DOCS } from '@/lib/email-template'
import type { UserRole, Currency, CurrencyDef } from '@/types'
import { CURRENCIES } from '@/types'

// Roles that may only ever be held as ADDITIONAL roles (granted per-user via
// identity's user_roles), never as a user's primary/base login role. Keep in
// step with ApiUserRole in services/users.ts, which deliberately excludes them.
// payment_officer in particular must not be primary: finance-api's _check_can_pay
// short-circuits on the primary role, so making it primary would hand out payment
// authority while bypassing the additional-role model entirely.
// Fallback for the primary-role filter, used only until GET /config/roles has
// answered (and if it ever fails). The live answer is identity's
// `role_defs.assignable_as_primary` — adding another additional-only role is a
// data change there, not an edit here.
const ADDITIONAL_ONLY_ROLES_FALLBACK = new Set<string>(['erp_pa_officer', 'payment_officer'])

/** ROLE_LABELS filtered down to roles selectable as a PRIMARY role — used by the
 *  user-create/edit Role <select> and the CSV import's validation/template, both
 *  of which write ApiUser.role. ROLE_LABELS itself stays complete (unfiltered)
 *  because it's still needed to display these roles wherever a user holds them
 *  as an additional role (search filter, table cell, etc).
 *
 *  `roles` is GET /config/roles; while it loads we fall back to the constant
 *  above, so the additional-only roles are never briefly selectable. */
function primaryRoleLabels(roles: CustomRole[] | undefined): Record<string, string> {
  const blocked = roles?.length
    ? new Set(roles.filter((r) => r.assignable_as_primary === false).map((r) => r.code))
    : ADDITIONAL_ONLY_ROLES_FALLBACK
  return Object.fromEntries(
    Object.entries(ROLE_LABELS).filter(([code]) => !blocked.has(code)),
  ) as Record<string, string>
}

// ─── Nav sections ─────────────────────────────────────────────────────────────

type Section =
  | 'company' | 'security' | 'currency' | 'email_templates' | 'pdf_templates'
  | 'users' | 'vendor_settings'
  | 'workflows' | 'service_gr_sla' | 'gr_notification_sla'
  | 'prepayment' | 'budget' | 'collection'
  | 'notifications' | 'pms_import'

interface NavEntry { id: Section; label: string; icon: React.ComponentType<{ className?: string }> }

const NAV: NavEntry[] = [
  { id: 'company',             label: 'Company Settings',       icon: Landmark },
  { id: 'security',            label: 'Security',               icon: ShieldCheck },
  { id: 'currency',            label: 'Currency Settings',      icon: Landmark },
  { id: 'email_templates',     label: 'Email Settings',         icon: Mail },
  { id: 'pdf_templates',       label: 'PDF Templates',          icon: FileText },
  { id: 'users',               label: 'User Management',        icon: Users },
  { id: 'vendor_settings',    label: 'Vendor Settings',        icon: Building2 },
  { id: 'workflows',           label: 'Approval Workflows',     icon: Workflow },
  { id: 'service_gr_sla',        label: 'Service GR SLA',         icon: Clock },
  { id: 'gr_notification_sla', label: 'GR Notification SLA',    icon: Bell },
  { id: 'prepayment',          label: 'Prepayment Config',      icon: CreditCard },
  { id: 'budget',              label: 'Budget Config',          icon: PiggyBank },
  { id: 'collection',          label: 'Collection Config',      icon: PackageCheck },
  { id: 'notifications',       label: 'Notification Settings',  icon: Bell },
  { id: 'pms_import',          label: 'PMS Data Import',        icon: Upload },
]

// ─── Shared helpers ───────────────────────────────────────────────────────────

const inputCls = (err?: boolean) =>
  cn(
    'h-10 w-full rounded-lg border bg-neutral-100 px-3 text-sm text-neutral-900 placeholder:text-neutral-400 focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors',
    err ? 'border-danger-500' : 'border-neutral-200'
  )

function SaveBar({ saved, onSave, label = 'Save Settings' }: { saved: boolean; onSave: () => void; label?: string }) {
  return (
    <div className="flex items-center gap-3 pt-4 border-t border-neutral-200">
      <Button onClick={onSave}>
        {saved && <Check className="h-4 w-4" />}
        {saved ? 'Saved!' : label}
      </Button>
      {saved && <span className="text-sm text-success-600 font-medium">Changes saved</span>}
    </div>
  )
}

function ToggleRow({ label, description, checked, onToggle }: {
  label: string; description: string; checked: boolean; onToggle: () => void
}) {
  return (
    <div className="flex items-start justify-between gap-6 rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-4">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-neutral-900">{label}</span>
        <span className="text-xs text-neutral-500">{description}</span>
        <span className={cn('mt-1 inline-flex w-fit items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
          checked ? 'bg-success-50 text-success-700' : 'bg-neutral-200 text-neutral-500')}>
          <span className={cn('h-1.5 w-1.5 rounded-full', checked ? 'bg-success-600' : 'bg-neutral-400')} />
          {checked ? 'Enabled' : 'Disabled'}
        </span>
      </div>
      <button type="button" role="switch" aria-checked={checked} onClick={onToggle}
        className={cn('relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary-600 focus:ring-offset-2',
          checked ? 'bg-primary-600' : 'bg-neutral-300')}>
        <span className={cn('absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0')} />
      </button>
    </div>
  )
}

function SlaRow({ label, description, value, onChange, min = 1 }: {
  label: string; description: string; value: number; onChange: (v: number) => void; min?: number
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-neutral-900">{label}</p>
        <p className="text-xs text-neutral-500 mt-0.5">{description}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <input
          type="number" min={min} max={30}
          value={value}
          onChange={(e) => onChange(Math.max(min, Number(e.target.value)))}
          className="h-9 w-16 rounded-lg border border-neutral-300 bg-white px-2 text-center text-sm font-semibold text-neutral-900 focus:outline-none focus:ring-2 focus:ring-primary-600"
        />
        <span className="text-xs text-neutral-500 whitespace-nowrap">business days</span>
      </div>
    </div>
  )
}

// ─── Company Settings ─────────────────────────────────────────────────────────

function CompanySettings() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [name, setName] = useState('')
  const [tagline, setTagline] = useState('')
  const [deliveryAddress, setDeliveryAddress] = useState('')
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewFileName, setPreviewFileName] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (config?.name !== undefined) setName(config.name) }, [config?.name])
  useEffect(() => { if (config?.tagline !== undefined) setTagline(config.tagline) }, [config?.tagline])
  useEffect(() => { if (config?.delivery_address !== undefined) setDeliveryAddress(config.delivery_address) }, [config?.delivery_address])
  useEffect(() => { if (config?.logo_data_url !== undefined) setPreviewUrl(config.logo_data_url) }, [config?.logo_data_url])
  useEffect(() => { if (config?.logo_file_name !== undefined) setPreviewFileName(config.logo_file_name) }, [config?.logo_file_name])

  const loadFile = (file: File) => {
    if (!file.type.startsWith('image/')) return
    const reader = new FileReader()
    reader.onload = (e) => { setPreviewUrl(e.target?.result as string); setPreviewFileName(file.name) }
    reader.readAsDataURL(file)
  }
  const handleDrop = (e: React.DragEvent) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files?.[0]; if (f) loadFile(f) }

  const handleSave = () => {
    updateConfig.mutate({ name: name.trim() || 'EPMS', tagline: tagline.trim(), delivery_address: deliveryAddress.trim(), logo_data_url: previewUrl, logo_file_name: previewFileName })
    setSaved(true); setTimeout(() => setSaved(false), 2500)
  }

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Company Information</h3>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">Company Name <span className="text-danger-600">*</span></label>
          <input className={inputCls()} value={name} onChange={(e) => setName(e.target.value)} placeholder="Canada Royal Milk" />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">Tagline / Subtitle</label>
          <input className={inputCls()} value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="Enterprise Procurement Management" />
          <p className="text-xs text-neutral-400">Shown below the company name in the sidebar</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">Default Delivery Address</label>
          <textarea rows={3} className={inputCls() + ' py-2 resize-none'} value={deliveryAddress} onChange={(e) => setDeliveryAddress(e.target.value)} placeholder="123 Warehouse Rd, Toronto, ON M5V 1A1" />
          <p className="text-xs text-neutral-400">Pre-filled in PR and PO forms. Users can override per order.</p>
        </div>
      </section>

      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Company Logo</h3>
        {previewUrl && (
          <div className="flex items-center gap-4 rounded-xl border border-neutral-200 bg-neutral-50 p-4">
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg bg-white shadow overflow-hidden">
              <img src={previewUrl} alt="Logo preview" className="h-full w-full object-contain p-1" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-neutral-900 truncate">{previewFileName ?? 'Uploaded logo'}</p>
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => fileRef.current?.click()} className="text-xs font-medium text-primary-600 hover:underline">Replace</button>
                <span className="text-neutral-300">·</span>
                <button onClick={() => { setPreviewUrl(null); setPreviewFileName(null); if (fileRef.current) fileRef.current.value = '' }} className="text-xs font-medium text-danger-600 hover:underline">Remove</button>
              </div>
            </div>
          </div>
        )}
        <div onDragOver={(e) => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={handleDrop} onClick={() => fileRef.current?.click()}
          className={cn('flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 cursor-pointer transition-colors',
            dragging ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-neutral-50 hover:border-primary-300 hover:bg-primary-50/50')}>
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-100">
            <ImagePlus className="h-5 w-5 text-primary-600" />
          </div>
          <p className="text-sm font-medium text-neutral-700">{previewUrl ? 'Upload a different logo' : 'Upload company logo'}</p>
          <p className="text-xs text-neutral-400">PNG, JPG, SVG · Transparent or white background recommended</p>
        </div>
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) loadFile(f) }} />
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Sidebar Preview</h3>
        <div className="inline-flex items-center gap-2.5 rounded-xl bg-[#085E5E] px-3 py-2.5 w-fit">
          {previewUrl ? (
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white overflow-hidden">
              <img src={previewUrl} alt="" className="h-full w-full object-contain p-0.5" />
            </div>
          ) : (
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
              <span className="text-xs font-bold text-white">{(name || 'EP').split(/\s+/).map((w) => w[0]).join('').toUpperCase().slice(0, 2)}</span>
            </div>
          )}
          <div>
            <p className="text-sm font-bold text-white/95 leading-tight">{name || 'Company Name'}</p>
            {tagline && <p className="text-[10px] text-white/45 leading-tight">{tagline}</p>}
          </div>
        </div>
      </section>

      <SaveBar saved={saved} onSave={handleSave} label="Save Settings" />
    </div>
  )
}

// ─── MFA Personal Setup ───────────────────────────────────────────────────────

function MfaPersonalSetup({ currentUserMfaEnabled }: { currentUserMfaEnabled: boolean }) {
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [isError, setIsError] = useState(false)
  const queryClient = useQueryClient()

  const toggle = async () => {
    setLoading(true); setMessage(''); setIsError(false)
    try {
      if (currentUserMfaEnabled) {
        await authService.mfaDisable()
        setMessage('Email MFA has been disabled on your account.')
      } else {
        await authService.mfaEnable()
        setMessage('Email MFA enabled. A verification code will be sent to your email at next login.')
      }
      queryClient.invalidateQueries({ queryKey: ['users'] })
    } catch (e) {
      setIsError(true)
      setMessage(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-neutral-900">My Account MFA</p>
          <p className="text-xs text-neutral-500 mt-0.5">
            {currentUserMfaEnabled
              ? 'Email verification is active on your account.'
              : 'Enable to require an email code at each login.'}
          </p>
        </div>
        <span className={cn('inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
          currentUserMfaEnabled ? 'bg-success-50 text-success-700' : 'bg-neutral-100 text-neutral-500')}>
          {currentUserMfaEnabled ? '● Enabled' : '○ Disabled'}
        </span>
      </div>

      {message && (
        <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-xs border',
          isError ? 'bg-danger-50 border-danger-200 text-danger-700' : 'bg-success-50 border-success-200 text-success-700')}>
          <Check className="h-3.5 w-3.5 shrink-0" />{message}
        </div>
      )}

      <div className="flex gap-2">
        {currentUserMfaEnabled ? (
          <button onClick={toggle} disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-danger-300 bg-white px-3 py-1.5 text-xs font-medium text-danger-600 hover:bg-danger-50 disabled:opacity-50">
            <X className="h-3.5 w-3.5" />{loading ? 'Disabling…' : 'Disable MFA'}
          </button>
        ) : (
          <Button size="sm" onClick={toggle} disabled={loading}>
            <Mail className="h-3.5 w-3.5" />{loading ? 'Enabling…' : 'Enable Email MFA'}
          </Button>
        )}
      </div>
    </div>
  )
}

// ─── Security Settings ────────────────────────────────────────────────────────

const EXPIRY_OPTIONS: { label: string; value: number | null }[] = [
  { label: 'Never expire', value: null },
  { label: '30 days', value: 30 },
  { label: '60 days', value: 60 },
  { label: '90 days (recommended)', value: 90 },
  { label: '180 days', value: 180 },
  { label: '365 days', value: 365 },
]

function SecuritySettings() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const { user } = useAuthStore()
  const { data: userData } = useUsers()
  const [saved, setSaved] = useState(false)
  const [smtpSaved, setSmtpSaved] = useState(false)
  const mfaEnabled = config?.mfa_enabled ?? false
  const passwordExpiryDays = config?.password_expiry_days ?? null
  const save = (patch: Parameters<typeof updateConfig.mutate>[0]) => { updateConfig.mutate(patch); setSaved(true); setTimeout(() => setSaved(false), 2500) }

  // Current logged-in user's per-account MFA status from the users list
  const currentUserRecord = userData?.items.find((u) => u.email === user?.email)
  const currentUserMfaEnabled = currentUserRecord?.mfa_enabled ?? false

  // SMTP form state
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState('')
  const [smtpUser, setSmtpUser] = useState('')
  const [smtpPassword, setSmtpPassword] = useState('')
  const [smtpUseTls, setSmtpUseTls] = useState(false)
  const [smtpFrom, setSmtpFrom] = useState('')
  const [showSmtpPassword, setShowSmtpPassword] = useState(false)
  const [testTo, setTestTo] = useState('')
  const [testStatus, setTestStatus] = useState<'idle' | 'sending' | 'ok' | 'error'>('idle')
  const [testMsg, setTestMsg] = useState('')

  useEffect(() => {
    if (!config) return
    setSmtpHost(config.smtp_host ?? '')
    setSmtpPort(config.smtp_port != null ? String(config.smtp_port) : '')
    setSmtpUser(config.smtp_user ?? '')
    setSmtpPassword(config.smtp_password ?? '')
    setSmtpUseTls(config.smtp_use_tls ?? false)
    setSmtpFrom(config.smtp_from ?? '')
  }, [config?.smtp_host, config?.smtp_port, config?.smtp_user, config?.smtp_password, config?.smtp_use_tls, config?.smtp_from])

  // Pre-fill test recipient with current user's email
  const { user: currentUser } = useAuthStore()
  useEffect(() => { if (currentUser?.email && !testTo) setTestTo(currentUser.email) }, [currentUser?.email])

  const handleSmtpSave = () => {
    updateConfig.mutate({
      smtp_host: smtpHost || null,
      smtp_port: smtpPort ? Number(smtpPort) : null,
      smtp_user: smtpUser || null,
      smtp_password: smtpPassword || null,
      smtp_use_tls: smtpUseTls,
      smtp_from: smtpFrom || null,
    })
    setSmtpSaved(true); setTimeout(() => setSmtpSaved(false), 2500)
  }

  const handleTestSmtp = async () => {
    if (!testTo) return
    setTestStatus('sending'); setTestMsg('')
    try {
      const res = await configService.testSmtp(testTo)
      setTestStatus('ok'); setTestMsg(res.message)
    } catch (e) {
      setTestStatus('error')
      setTestMsg(e instanceof Error ? e.message : 'Connection failed')
    }
  }

  const inputCls = 'h-9 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
  const labelCls = 'text-xs font-medium text-neutral-700'

  return (
    <div className="flex flex-col gap-8 max-w-lg">
      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Multi-Factor Authentication</h3>
        <ToggleRow label="Require MFA for all users" description="Every user must verify a 6-digit code sent to their registered email address after login." checked={mfaEnabled} onToggle={() => save({ mfa_enabled: !mfaEnabled })} />
        {!mfaEnabled && (
          <div className="rounded-lg bg-primary-50 border border-primary-100 px-4 py-3 text-xs text-primary-700">
            <p className="font-medium mb-0.5">When MFA is disabled globally</p>
            <p className="text-primary-600">Users without personal MFA enabled skip the email verification step and go directly to the dashboard.</p>
          </div>
        )}
        <MfaPersonalSetup currentUserMfaEnabled={currentUserMfaEnabled} />
      </section>

      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">SMTP Configuration</h3>
        <p className="text-xs text-neutral-500 -mt-2">
          Override the server's default SMTP settings. Leave fields blank to use the server defaults (.env).
        </p>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <label className={labelCls}>SMTP Host</label>
              <input className={inputCls} placeholder="smtp.example.com" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className={labelCls}>Port</label>
              <input className={inputCls} type="number" placeholder="587" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <label className={labelCls}>Username</label>
              <input className={inputCls} placeholder="user@example.com" autoComplete="off" value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className={labelCls}>Password</label>
              <div className="relative">
                <input
                  className={cn(inputCls, 'pr-9')}
                  type={showSmtpPassword ? 'text' : 'password'}
                  placeholder="••••••••"
                  autoComplete="new-password"
                  value={smtpPassword}
                  onChange={(e) => setSmtpPassword(e.target.value)}
                />
                <button type="button" onClick={() => setShowSmtpPassword((p) => !p)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                  {showSmtpPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className={labelCls}>From Address</label>
            <input className={inputCls} placeholder="noreply@example.com" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} />
          </div>
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <button type="button" role="switch" aria-checked={smtpUseTls} onClick={() => setSmtpUseTls((p) => !p)}
                className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary-600 focus:ring-offset-2',
                  smtpUseTls ? 'bg-primary-600' : 'bg-neutral-300')}>
                <span className={cn('absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', smtpUseTls ? 'translate-x-4' : 'translate-x-0')} />
              </button>
              <span className="text-sm text-neutral-700">Use TLS/SSL</span>
            </label>
            <div className="flex items-center gap-3">
              {smtpSaved && <span className="flex items-center gap-1 text-xs text-success-600"><Check className="h-3.5 w-3.5" />Saved</span>}
              <Button size="sm" onClick={handleSmtpSave}>Save SMTP</Button>
            </div>
          </div>

          <div className="border-t border-neutral-200 pt-4 flex flex-col gap-2">
            <p className="text-xs font-medium text-neutral-700">Test Connection</p>
            <div className="flex gap-2">
              <input
                className={cn(inputCls, 'flex-1')}
                type="email"
                placeholder="recipient@example.com"
                value={testTo}
                onChange={(e) => { setTestTo(e.target.value); setTestStatus('idle'); setTestMsg('') }}
              />
              <Button size="sm" variant="secondary" onClick={handleTestSmtp} disabled={!testTo || testStatus === 'sending'}>
                {testStatus === 'sending' ? 'Sending…' : 'Send Test Email'}
              </Button>
            </div>
            {testStatus === 'ok' && (
              <p className="flex items-center gap-1.5 text-xs text-success-600"><Check className="h-3.5 w-3.5" />{testMsg}</p>
            )}
            {testStatus === 'error' && (
              <p className="text-xs text-danger-600">{testMsg}</p>
            )}
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Password Policy</h3>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-neutral-900">Password Expiry</span>
            <span className="text-xs text-neutral-500">Users will be forced to change their password after this period.</span>
          </div>
          <div className="flex items-center gap-3">
            <select value={passwordExpiryDays ?? ''} onChange={(e) => { const val = e.target.value === '' ? null : Number(e.target.value); save({ password_expiry_days: val }) }}
              className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
              {EXPIRY_OPTIONS.map((o) => (<option key={String(o.value)} value={o.value ?? ''}>{o.label}</option>))}
            </select>
            {passwordExpiryDays
              ? <span className="inline-flex items-center gap-1.5 rounded-full bg-warning-50 px-2.5 py-0.5 text-xs font-medium text-warning-700"><span className="h-1.5 w-1.5 rounded-full bg-warning-500" />Expires every {passwordExpiryDays} days</span>
              : <span className="inline-flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-medium text-neutral-500">No expiry</span>
            }
          </div>
        </div>
      </section>
      {saved && <p className="flex items-center gap-1.5 text-sm text-success-600"><Check className="h-4 w-4" />Setting saved</p>}
    </div>
  )
}

// ─── Currency Settings ────────────────────────────────────────────────────────

const BLANK_CURRENCY: CurrencyDef = { value: '', label: '', symbol: '' }

function CurrencyRow({ c, isDefault, isEnabled, canToggle, onSetDefault, onToggle, onEdit, onDelete }: {
  c: CurrencyDef; isDefault: boolean; isEnabled: boolean; canToggle: boolean
  onSetDefault: () => void; onToggle: () => void; onEdit?: () => void; onDelete?: () => void
}) {
  return (
    <div className={cn('flex items-center justify-between rounded-xl border px-4 py-3 transition-colors', isEnabled ? 'border-neutral-200 bg-white' : 'border-neutral-100 bg-neutral-50 opacity-60')}>
      <div className="flex items-center gap-3 min-w-0">
        <span className="w-10 shrink-0 text-center font-mono text-sm font-semibold text-neutral-700">{c.symbol}</span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-neutral-900">{c.value}</p>
          <p className="text-xs text-neutral-500 truncate">{c.label}</p>
        </div>
        {isDefault && <span className="ml-1 inline-flex items-center rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 shrink-0">Default</span>}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {isEnabled && !isDefault && <button type="button" onClick={onSetDefault} className="text-xs text-primary-600 hover:underline whitespace-nowrap">Set as default</button>}
        {onEdit && <button type="button" onClick={onEdit} className="flex h-6 w-6 items-center justify-center rounded text-neutral-400 hover:text-primary-600 hover:bg-primary-50 transition-colors"><Pencil className="h-3.5 w-3.5" /></button>}
        {onDelete && <button type="button" onClick={onDelete} className="flex h-6 w-6 items-center justify-center rounded text-neutral-300 hover:text-danger-500 hover:bg-danger-50 transition-colors"><Trash2 className="h-3.5 w-3.5" /></button>}
        {canToggle && (
          <button type="button" role="switch" aria-checked={isEnabled} disabled={isDefault} onClick={onToggle}
            className={cn('relative h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary-600 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50', isEnabled ? 'bg-primary-600' : 'bg-neutral-300')}>
            <span className={cn('absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform', isEnabled ? 'translate-x-5' : 'translate-x-0')} />
          </button>
        )}
      </div>
    </div>
  )
}

function CurrencyEditForm({ form, onChange, onSave, onCancel, error, isNew }: {
  form: CurrencyDef
  onChange: (f: CurrencyDef) => void
  onSave: () => void
  onCancel: () => void
  error: string
  isNew: boolean
}) {
  return (
    <div className="rounded-xl border-2 border-primary-300 bg-primary-50 p-4 flex flex-col gap-3">
      <p className="text-xs font-semibold text-primary-700">{isNew ? 'Add Custom Currency' : 'Edit Currency'}</p>
      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-600">Code <span className="text-danger-600">*</span></label>
          <input
            value={form.value}
            onChange={(e) => onChange({ ...form, value: e.target.value.toUpperCase().slice(0, 10) })}
            placeholder="e.g. GBP"
            disabled={!isNew}
            className="h-9 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-500 uppercase"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-600">Symbol <span className="text-danger-600">*</span></label>
          <input
            value={form.symbol}
            onChange={(e) => onChange({ ...form, symbol: e.target.value.slice(0, 5) })}
            placeholder="e.g. £"
            className="h-9 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-600">Name <span className="text-danger-600">*</span></label>
          <input
            value={form.label}
            onChange={(e) => onChange({ ...form, label: e.target.value })}
            placeholder="e.g. British Pound"
            className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
          />
        </div>
      </div>
      {error && <p className="text-xs text-danger-600">{error}</p>}
      <div className="flex gap-2 justify-end">
        <button type="button" onClick={onCancel} className="px-3 py-1.5 text-xs font-medium text-neutral-600 hover:text-neutral-900 rounded-lg hover:bg-neutral-100 transition-colors">Cancel</button>
        <button type="button" onClick={onSave} className="px-3 py-1.5 text-xs font-semibold text-white bg-primary-600 rounded-lg hover:bg-primary-700 transition-colors">
          {isNew ? 'Add Currency' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}

function CurrencySettings() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [defaultCurrency, setDefaultCurrency] = useState<Currency>('CAD')
  const [enabled, setEnabled] = useState<Set<Currency>>(new Set(['CAD']))
  const [customCurrencies, setCustomCurrencies] = useState<CurrencyDef[]>([])
  const [saved, setSaved] = useState(false)

  // Edit state: null = idle, 'new' = adding, string = editing that code
  const [editingKey, setEditingKey] = useState<string | 'new' | null>(null)
  const [editForm, setEditForm] = useState<CurrencyDef>(BLANK_CURRENCY)
  const [editError, setEditError] = useState('')

  useEffect(() => { if (config?.default_currency) setDefaultCurrency(config.default_currency) }, [config?.default_currency])
  useEffect(() => { if (config?.enabled_currencies) setEnabled(new Set(config.enabled_currencies)) }, [config?.enabled_currencies])
  useEffect(() => { if (config?.custom_currencies) setCustomCurrencies(config.custom_currencies) }, [config?.custom_currencies])

  const allCodes = [...CURRENCIES.map((c) => c.value), ...customCurrencies.map((c) => c.value)]

  const toggleEnabled = (code: Currency) =>
    setEnabled((p) => { const n = new Set(p); if (n.has(code)) { if (code === defaultCurrency || n.size <= 1) return p; n.delete(code) } else n.add(code); return n })

  const setDefault = (code: Currency) => { setDefaultCurrency(code); setEnabled((p) => new Set([...p, code])) }

  const handleSave = () => {
    updateConfig.mutate({
      default_currency: defaultCurrency,
      enabled_currencies: allCodes.filter((c) => enabled.has(c)),
      custom_currencies: customCurrencies,
    })
    setSaved(true); setTimeout(() => setSaved(false), 2500)
  }

  const startAdd = () => { setEditingKey('new'); setEditForm(BLANK_CURRENCY); setEditError('') }

  const startEdit = (c: CurrencyDef) => { setEditingKey(c.value); setEditForm({ ...c }); setEditError('') }

  const cancelEdit = () => { setEditingKey(null); setEditError('') }

  const commitEdit = () => {
    const code = editForm.value.trim().toUpperCase()
    const label = editForm.label.trim()
    const symbol = editForm.symbol.trim()
    if (!code) { setEditError('Currency code is required'); return }
    if (!label) { setEditError('Currency name is required'); return }
    if (!symbol) { setEditError('Symbol is required'); return }
    if (editingKey === 'new' && allCodes.includes(code)) { setEditError(`Currency code "${code}" already exists`); return }

    if (editingKey === 'new') {
      setCustomCurrencies((p) => [...p, { value: code, label, symbol }])
      setEnabled((p) => new Set([...p, code]))
    } else {
      setCustomCurrencies((p) => p.map((c) => c.value === editingKey ? { value: code, label, symbol } : c))
    }
    setEditingKey(null)
    setEditError('')
  }

  const deleteCustom = (code: string) => {
    setCustomCurrencies((p) => p.filter((c) => c.value !== code))
    setEnabled((p) => { const n = new Set(p); n.delete(code); return n })
    if (defaultCurrency === code) setDefaultCurrency('CAD')
    if (editingKey === code) setEditingKey(null)
  }

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      {/* Built-in */}
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Built-in Currencies</h3>
        <div className="flex flex-col gap-2">
          {CURRENCIES.map((c) => (
            <CurrencyRow key={c.value} c={c}
              isDefault={defaultCurrency === c.value}
              isEnabled={enabled.has(c.value)}
              canToggle
              onSetDefault={() => setDefault(c.value)}
              onToggle={() => toggleEnabled(c.value)}
            />
          ))}
        </div>
      </section>

      {/* Custom */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Custom Currencies</h3>
          {editingKey !== 'new' && (
            <button type="button" onClick={startAdd}
              className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-primary-600 hover:bg-primary-50 border border-primary-200 transition-colors">
              <Plus className="h-3.5 w-3.5" /> Add Currency
            </button>
          )}
        </div>

        {customCurrencies.length === 0 && editingKey !== 'new' && (
          <p className="text-xs text-neutral-400 italic px-1">No custom currencies defined yet.</p>
        )}

        <div className="flex flex-col gap-2">
          {customCurrencies.map((c) =>
            editingKey === c.value ? (
              <CurrencyEditForm key={c.value}
                form={editForm} onChange={setEditForm}
                onSave={commitEdit} onCancel={cancelEdit}
                error={editError} isNew={false}
              />
            ) : (
              <CurrencyRow key={c.value} c={c}
                isDefault={defaultCurrency === c.value}
                isEnabled={enabled.has(c.value)}
                canToggle
                onSetDefault={() => setDefault(c.value)}
                onToggle={() => toggleEnabled(c.value)}
                onEdit={() => startEdit(c)}
                onDelete={() => deleteCustom(c.value)}
              />
            )
          )}

          {editingKey === 'new' && (
            <CurrencyEditForm
              form={editForm} onChange={setEditForm}
              onSave={commitEdit} onCancel={cancelEdit}
              error={editError} isNew
            />
          )}
        </div>
      </section>

      <SaveBar saved={saved} onSave={handleSave} />
    </div>
  )
}

// ─── Email Settings (renamed from Email Templates 2026-05-28) ─────────────────
//
// Section now covers BOTH the PO email template AND the PO-to-vendor SMTP
// profile (po_smtp_*). Internal task notification SMTP lives in Portal Admin
// → Notification Settings; this page is EPMS-specific because PO send-to-vendor
// is the only EPMS flow that emails external parties.

function PoSmtpSettings() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [form, setForm] = useState({ host: '', port: '', user: '', password: '', from: '', use_tls: true as boolean })
  const [hydrated, setHydrated] = useState(false)
  const [showPwd, setShowPwd] = useState(false)
  const [saved, setSaved] = useState(false)
  const [testTo, setTestTo] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null)

  // One-shot hydration from config so the user's later edits aren't blown away
  // every time the cached config refreshes.
  useEffect(() => {
    if (!hydrated && config) {
      setForm({
        host: config.po_smtp_host ?? '',
        port: config.po_smtp_port != null ? String(config.po_smtp_port) : '',
        user: config.po_smtp_user ?? '',
        password: config.po_smtp_password ?? '',
        from: config.po_smtp_from ?? '',
        use_tls: config.po_smtp_use_tls ?? true,
      })
      setHydrated(true)
    }
  }, [config, hydrated])

  const handleSave = () => {
    updateConfig.mutate({
      po_smtp_host: form.host || null,
      po_smtp_port: form.port ? parseInt(form.port) : null,
      po_smtp_user: form.user || null,
      po_smtp_password: form.password || null,
      po_smtp_from: form.from || null,
      po_smtp_use_tls: form.use_tls,
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  const handleTest = async () => {
    if (!testTo) return
    setTesting(true); setTestResult(null)
    try {
      await configService.testSmtp(testTo, 'po')
      setTestResult({ ok: true, msg: `Test PO email sent to ${testTo}` })
    } catch (e: any) {
      setTestResult({ ok: false, msg: e.message ?? 'Test failed' })
    } finally { setTesting(false) }
  }

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-neutral-200 bg-white p-5">
      <div>
        <h3 className="text-sm font-semibold text-neutral-900">PO Email SMTP — External Vendors</h3>
        <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
          Dedicated mailbox used when sending a Purchase Order email to a vendor. Leave any field blank to fall back
          to the internal Task Notification SMTP profile (Portal → Admin → Notification Settings) for that field.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">SMTP Host</label>
          <Input value={form.host} onChange={(e) => setForm((p) => ({ ...p, host: e.target.value }))} placeholder="smtp.example.com" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">Port</label>
          <Input type="number" value={form.port} onChange={(e) => setForm((p) => ({ ...p, port: e.target.value }))} placeholder="587" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">Username</label>
          <Input value={form.user} onChange={(e) => setForm((p) => ({ ...p, user: e.target.value }))} placeholder="purchasing@company.com" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">Password</label>
          <div className="relative">
            <Input
              type={showPwd ? 'text' : 'password'}
              value={form.password}
              onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
            />
            <button
              type="button"
              onClick={() => setShowPwd((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
            >
              {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">From Address</label>
          <Input value={form.from} onChange={(e) => setForm((p) => ({ ...p, from: e.target.value }))} placeholder="purchasing@company.com" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">TLS</label>
          <label className="flex items-center gap-2 mt-1">
            <input
              type="checkbox"
              checked={form.use_tls}
              onChange={(e) => setForm((p) => ({ ...p, use_tls: e.target.checked }))}
              className="h-4 w-4 rounded border-neutral-300"
            />
            <span className="text-xs text-neutral-500">Use TLS/STARTTLS</span>
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-neutral-100">
        <Button onClick={handleSave}>
          {saved && <Check className="h-4 w-4" />}
          {saved ? 'Saved!' : 'Save PO SMTP'}
        </Button>
        <div className="flex flex-1 items-center gap-2 min-w-[260px]">
          <Input
            className="flex-1"
            value={testTo}
            type="email"
            onChange={(e) => setTestTo(e.target.value)}
            placeholder="Send test PO email to…"
          />
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !testTo}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 whitespace-nowrap"
          >
            {testing ? 'Sending…' : 'Test'}
          </button>
        </div>
      </div>
      {testResult && (
        <p className={cn('text-xs', testResult.ok ? 'text-success-600' : 'text-danger-600')}>
          {testResult.msg}
        </p>
      )}
    </section>
  )
}

function EmailTemplates() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [saved, setSaved] = useState(false)
  const [showVars, setShowVars] = useState(false)

  useEffect(() => { if (config?.po_email_subject !== undefined) setSubject(config.po_email_subject) }, [config?.po_email_subject])
  useEffect(() => { if (config?.po_email_body !== undefined) setBody(config.po_email_body) }, [config?.po_email_body])

  const handleSave = () => { updateConfig.mutate({ po_email_subject: subject, po_email_body: body }); setSaved(true); setTimeout(() => setSaved(false), 2500) }
  const handleReset = () => {
    setSubject('Purchase Order {{po_number}} — {{company_name}}')
    setBody(`Dear {{vendor_name}},\n\nPlease find below our Purchase Order for your reference.\n\nPO Number:          {{po_number}}\nPO Date:            {{po_date}}\nExpected Delivery:  {{expected_delivery}}\nDelivery Address:   {{delivery_address}}\n\nITEMS ORDERED\n{{line_items}}\n\nSubtotal:   {{subtotal}}\nTax:        {{tax}}\nTotal:      {{total}}\n\nPayment Terms: Net 30 days from invoice date.\n\nPlease confirm receipt of this order and advise of any issues with availability or delivery dates.\n\nThis PO was issued via {{company_name}} procurement system. Please reference the PO number on all correspondence and invoices.\n\nRegards,\n{{sender_name}}\n{{company_name}}\n`)
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <PoSmtpSettings />
      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">PO Email Template</h3>
        <p className="text-sm text-neutral-500">Used when a Procurement Officer selects "Email Supplier" while placing an order. Sent via the SMTP server configured above.</p>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">Email Subject <span className="text-danger-600">*</span></label>
          <input className={cn(inputCls(), 'bg-neutral-100')} value={subject} onChange={(e) => setSubject(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-700">Email Body <span className="text-danger-600">*</span></label>
          <textarea className={cn(inputCls(), 'min-h-[200px] h-auto py-2.5 resize-y font-mono text-xs leading-relaxed')} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
      </section>
      <section className="flex flex-col gap-3">
        <button type="button" onClick={() => setShowVars((v) => !v)} className="flex items-center gap-2 text-sm font-medium text-primary-600 hover:underline w-fit">
          {showVars ? '▾' : '▸'} Available template variables
        </button>
        {showVars && (
          <div className="rounded-xl border border-neutral-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead><tr className="bg-neutral-50 border-b border-neutral-200">
                <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-52">Variable</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
              </tr></thead>
              <tbody>{TEMPLATE_VARIABLE_DOCS.map((v, i) => (
                <tr key={v.variable} className={cn('border-b border-neutral-100 last:border-0', i % 2 === 1 ? 'bg-neutral-50' : 'bg-white')}>
                  <td className="px-4 py-2 font-mono text-xs text-primary-700">{v.variable}</td>
                  <td className="px-4 py-2 text-xs text-neutral-600">{v.description}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      <div className="flex items-center gap-3 pt-2 border-t border-neutral-200">
        <Button onClick={handleSave}>{saved && <Check className="h-4 w-4" />}{saved ? 'Saved!' : 'Save Template'}</Button>
        <Button variant="secondary" onClick={handleReset}>Reset to Default</Button>
        {saved && <span className="text-sm text-success-600 font-medium">Template saved</span>}
      </div>
    </div>
  )
}

// ─── PDF Templates ────────────────────────────────────────────────────────────

const PDF_DOC_TYPES = [
  { key: 'pr' as const, label: 'Purchase Requisition (PR)', trigger: 'Auto-generated when PR is approved' },
  { key: 'po' as const, label: 'Purchase Order (PO)',       trigger: 'Auto-generated when PO is approved' },
  { key: 'gr' as const, label: 'Goods Receipt (GR)',        trigger: 'Auto-generated when GR is confirmed' },
  { key: 'pa' as const, label: 'Payment Application (PA)',  trigger: 'Auto-generated when PA is created' },
]

type PdfTemplatesState = Record<'pr' | 'po' | 'gr' | 'pa', PdfTemplateSettings>

const DEFAULT_PDF_TEMPLATES: PdfTemplatesState = {
  pr: { show_logo: true, header_note: '', footer_note: '', show_terms: false, terms_text: 'This Purchase Requisition is a formal internal request and does not constitute a binding commitment to the vendor until a Purchase Order is issued.' },
  po: { show_logo: true, header_note: '', footer_note: '', show_terms: true,  terms_text: 'Payment terms: Net 30 days from invoice date. Please confirm receipt of this Purchase Order and advise of any issues with availability or delivery dates.' },
  gr: { show_logo: true, header_note: '', footer_note: '', show_terms: false, terms_text: '' },
  pa: { show_logo: true, header_note: '', footer_note: '', show_terms: false, terms_text: '' },
}

function PdfTemplates() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [local, setLocal] = useState<PdfTemplatesState>(DEFAULT_PDF_TEMPLATES)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (config?.pdf_templates) {
      setLocal({
        pr: { ...DEFAULT_PDF_TEMPLATES.pr, ...config.pdf_templates.pr },
        po: { ...DEFAULT_PDF_TEMPLATES.po, ...config.pdf_templates.po },
        gr: { ...DEFAULT_PDF_TEMPLATES.gr, ...config.pdf_templates.gr },
        pa: { ...DEFAULT_PDF_TEMPLATES.pa, ...config.pdf_templates.pa },
      })
    }
  }, [config?.pdf_templates])

  const handleSave = () => { updateConfig.mutate({ pdf_templates: local }); setSaved(true) }

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">Configure what appears on each type of system-generated PDF.</p>
      {PDF_DOC_TYPES.map(({ key, label, trigger }) => {
        const v = local[key]
        const onChange = (next: PdfTemplateSettings) => setLocal((p) => ({ ...p, [key]: next }))
        return (
          <div key={key} className="rounded-lg border border-neutral-200 p-4 flex flex-col gap-4">
            <div><p className="text-sm font-semibold text-neutral-900">{label}</p><p className="text-xs text-neutral-400 mt-0.5">{trigger}</p></div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex items-center gap-2 cursor-pointer select-none"><input type="checkbox" checked={v.show_logo} onChange={(e) => onChange({ ...v, show_logo: e.target.checked })} className="h-4 w-4 rounded border-neutral-300 text-primary-600" /><span className="text-sm text-neutral-700">Show company logo</span></label>
              <label className="flex items-center gap-2 cursor-pointer select-none"><input type="checkbox" checked={v.show_terms} onChange={(e) => onChange({ ...v, show_terms: e.target.checked })} className="h-4 w-4 rounded border-neutral-300 text-primary-600" /><span className="text-sm text-neutral-700">Show Terms &amp; Conditions</span></label>
            </div>
            {(['header_note', 'footer_note'] as const).map((field) => (
              <div key={field} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700">{field === 'header_note' ? 'Header Note' : 'Footer Note'}</label>
                <input type="text" value={v[field]} onChange={(e) => onChange({ ...v, [field]: e.target.value })} placeholder={field === 'header_note' ? '"CONFIDENTIAL — Internal Use Only"' : '"For questions contact procurement@company.com"'}
                  className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
              </div>
            ))}
            {v.show_terms && (
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-700">Terms &amp; Conditions Text</label>
                <textarea value={v.terms_text} onChange={(e) => onChange({ ...v, terms_text: e.target.value })} rows={4} className="rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 resize-y" />
              </div>
            )}
          </div>
        )
      })}
      <div className="flex items-center gap-3 pt-1">
        <Button size="sm" onClick={handleSave}><Check className="h-3.5 w-3.5" />Save Changes</Button>
        <Button variant="secondary" size="sm" onClick={() => { setLocal(DEFAULT_PDF_TEMPLATES); updateConfig.mutate({ pdf_templates: DEFAULT_PDF_TEMPLATES }); setSaved(false) }}>Reset to Defaults</Button>
        {saved && <span className="text-xs text-success-600 font-medium flex items-center gap-1"><Check className="h-3 w-3" />Saved</span>}
      </div>
    </div>
  )
}

// ─── User form ────────────────────────────────────────────────────────────────

interface UserFormData { full_name: string; email: string; role: UserRole; erp_person_code: string; department_id: string; is_active: boolean; teams_account: string; supervisor_id: string }
const BLANK_USER: UserFormData = { full_name: '', email: '', role: 'requester', erp_person_code: '', department_id: '', is_active: true, teams_account: '', supervisor_id: '' }

// Standard initial password assigned to every new / imported account. Users are
// forced to change it on first login (must_change_password is set server-side).
const INITIAL_PASSWORD = 'Feihe12#$'

function UserForm({ initial, onSave, onCancel, title, saveError }: { initial: UserFormData; onSave: (d: UserFormData) => void; onCancel: () => void; title: string; saveError?: string | null }) {
  const rolesQ = useRoles()
  const { data: deptData } = useDepartments()
  const activeDepts = (deptData?.items ?? []).filter((d) => d.is_active)
  const { data: usersData } = useUsers()
  const activeUsers = (usersData?.items ?? []).filter((u) => u.is_active)
  const [form, setForm] = useState<UserFormData>(initial)
  const [errors, setErrors] = useState<Partial<Record<keyof UserFormData, string>>>({})
  const set = (k: keyof UserFormData, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }))
  const validate = () => {
    const e: typeof errors = {}
    if (!form.full_name.trim()) e.full_name = 'Required'
    if (!form.email.trim()) e.email = 'Required'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) e.email = 'Invalid email'
    if (!form.erp_person_code.trim()) e.erp_person_code = 'Required'
    if (!form.department_id) e.department_id = 'Required'
    setErrors(e); return Object.keys(e).length === 0
  }
  const fldCls = (err?: string) => cn('h-10 w-full rounded-md border bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600', err ? 'border-danger-600' : 'border-neutral-300')

  return (
    <div className="rounded-lg border border-primary-200 bg-primary-50 p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {[{ k: 'full_name' as const, label: 'Full Name', placeholder: 'Jane Smith', type: 'text' }, { k: 'email' as const, label: 'Email Address', placeholder: 'jane@company.ca', type: 'email' }].map(({ k, label, placeholder, type }) => (
          <div key={k} className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-700">{label}<span className="text-danger-600"> *</span></label>
            <input type={type} className={fldCls(errors[k])} value={form[k]} onChange={(e) => set(k, e.target.value)} placeholder={placeholder} />
            {errors[k] && <p className="text-xs text-danger-600">{errors[k]}</p>}
          </div>
        ))}
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">ERP Person Code<span className="text-danger-600"> *</span></label>
          <input type="text" className={fldCls(errors.erp_person_code)} value={form.erp_person_code} onChange={(e) => set('erp_person_code', e.target.value)} placeholder="ERP employee / person code" />
          {errors.erp_person_code && <p className="text-xs text-danger-600">{errors.erp_person_code}</p>}
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">Department <span className="text-danger-600">*</span></label>
          <select className={fldCls(errors.department_id)} value={form.department_id} onChange={(e) => set('department_id', e.target.value)}>
            <option value="">— Select department —</option>
            {activeDepts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          {errors.department_id && <p className="text-xs text-danger-600">{errors.department_id}</p>}
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-700">Role <span className="text-danger-600">*</span></label>
          <select className={fldCls()} value={form.role} onChange={(e) => set('role', e.target.value)}>
            {(Object.entries(primaryRoleLabels(rolesQ.data)) as [UserRole, string][]).map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-4 h-10">
          {[{ v: true, label: 'Active' }, { v: false, label: 'Inactive' }].map(({ v, label }) => (
            <label key={String(v)} className="flex items-center gap-2 cursor-pointer">
              <input type="radio" checked={form.is_active === v} onChange={() => set('is_active', v)} className="accent-primary-600" />
              <span className="text-sm text-neutral-700">{label}</span>
            </label>
          ))}
        </div>
        <div className="flex flex-col gap-1 sm:col-span-2">
          <label className="text-xs font-medium text-neutral-700">Teams Account <span className="text-neutral-400 font-normal">(UPN / email for Teams approval)</span></label>
          <input type="email" className={fldCls()} value={form.teams_account} onChange={(e) => set('teams_account', e.target.value)} placeholder="user@company.onmicrosoft.com" />
        </div>
        <div className="flex flex-col gap-1 sm:col-span-2">
          <label className="text-xs font-medium text-neutral-700">Supervisor <span className="text-neutral-400 font-normal">(optional — required when dept supervisor step is enabled)</span></label>
          <select className={fldCls()} value={form.supervisor_id} onChange={(e) => set('supervisor_id', e.target.value)}>
            <option value="">— No supervisor —</option>
            {activeUsers.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.department_name})</option>)}
          </select>
        </div>
      </div>
      {saveError && (
        <div className="mt-4 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {saveError}
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" onClick={() => { if (validate()) onSave(form) }}><Check className="h-3.5 w-3.5" />Save User</Button>
      </div>
    </div>
  )
}

// ─── CSV helpers (User import/export) ─────────────────────────────────────────

const CSV_HEADERS = ['full_name', 'email', 'role', 'erp_person_code', 'department', 'is_active', 'teams_account'] as const

interface CsvRow {
  full_name: string; email: string; role: string; erp_person_code: string; department: string; is_active: string; teams_account: string
}
interface ImportRow extends CsvRow {
  line: number
  errors: string[]
}

function parseUserCsv(text: string, validRoles: Set<string>): ImportRow[] {
  const lines = text.replace(/\r/g, '').split('\n').filter((l) => l.trim())
  if (lines.length < 2) return []
  // header row: normalise to lowercase trimmed
  const header = lines[0].split(',').map((h) => h.trim().toLowerCase())
  const colIdx = (name: string) => header.indexOf(name)

  return lines.slice(1).map((rawLine, i) => {
    // Handle quoted fields containing commas
    const cols: string[] = []
    let cur = '', inQuote = false
    for (const ch of rawLine) {
      if (ch === '"') { inQuote = !inQuote }
      else if (ch === ',' && !inQuote) { cols.push(cur.trim()); cur = '' }
      else { cur += ch }
    }
    cols.push(cur.trim())

    const get = (name: string) => (cols[colIdx(name)] ?? '').replace(/^"|"$/g, '').trim()
    const row: ImportRow = {
      line: i + 2,
      full_name: get('full_name') || get('name'), email: get('email'), role: get('role'),
      erp_person_code: get('erp_person_code') || get('erppersoncode') || get('erp_code'),
      department: get('department'),
      is_active: get('is_active') || get('isactive') || get('isActive'),
      teams_account: get('teams_account') || get('teamsaccount') || get('teams'),
      errors: [],
    }
    if (!row.full_name) row.errors.push('Name required')
    if (!row.email) row.errors.push('Email required')
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(row.email)) row.errors.push('Invalid email')
    if (!row.role) row.errors.push('Role required')
    else if (!validRoles.has(row.role)) row.errors.push(`Unknown role "${row.role}"`)
    if (!row.erp_person_code) row.errors.push('ERP person code required')
    if (!row.department) row.errors.push('Department required')
    const activeRaw = row.is_active.toLowerCase()
    if (activeRaw && !['true','false','yes','no','1','0'].includes(activeRaw)) row.errors.push('is_active must be true/false')
    return row
  })
}

function csvIsActive(val: string): boolean {
  return !val || ['true', 'yes', '1'].includes(val.toLowerCase())
}

function exportUsersCsv(users: ApiUser[]) {
  const escape = (v: string) => v.includes(',') ? `"${v}"` : v
  const rows = [
    CSV_HEADERS.join(','),
    ...users.map((u) => [u.full_name, u.email, u.role, u.erp_person_code ?? '', u.department_name ?? '', String(u.is_active), u.teams_account ?? ''].map(escape).join(',')),
  ]
  const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url
  a.download = `epms-users-${new Date().toISOString().slice(0,10)}.csv`
  a.click(); URL.revokeObjectURL(url)
}

function downloadTemplate(validRoles: string[]) {
  const header = CSV_HEADERS.join(',')
  const example = 'Jane Smith,jane.smith@company.ca,requester,EMP-0001,Marketing,true,jane.smith@company.onmicrosoft.com'
  const roleNote = `# Valid roles: ${validRoles.join(' | ')}`
  const blob = new Blob([[header, example, roleNote].join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url
  a.download = 'epms-users-template.csv'
  a.click(); URL.revokeObjectURL(url)
}

// ─── Import preview panel ──────────────────────────────────────────────────────

function ImportPanel({ rows, existingEmails, onConfirm, onCancel }: {
  rows: ImportRow[]
  existingEmails: Set<string>
  onConfirm: (rows: ImportRow[]) => void
  onCancel: () => void
}) {
  const valid   = rows.filter((r) => r.errors.length === 0 && !existingEmails.has(r.email.toLowerCase()))
  const skipped = rows.filter((r) => r.errors.length === 0 && existingEmails.has(r.email.toLowerCase()))
  const invalid = rows.filter((r) => r.errors.length > 0)

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50 p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-neutral-900">Import Preview</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600"><X className="h-4 w-4" /></button>
      </div>

      {/* Summary chips */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-neutral-100 px-3 py-1 text-xs font-medium text-neutral-600">{rows.length} rows total</span>
        {valid.length > 0 && <span className="inline-flex items-center gap-1.5 rounded-full bg-success-50 px-3 py-1 text-xs font-medium text-success-700"><Check className="h-3 w-3" />{valid.length} will be added</span>}
        {skipped.length > 0 && <span className="inline-flex items-center gap-1.5 rounded-full bg-warning-50 px-3 py-1 text-xs font-medium text-warning-700">{skipped.length} duplicate email — skipped</span>}
        {invalid.length > 0 && <span className="inline-flex items-center gap-1.5 rounded-full bg-danger-50 px-3 py-1 text-xs font-medium text-danger-600"><X className="h-3 w-3" />{invalid.length} errors</span>}
      </div>

      {/* Row table */}
      <div className="max-h-72 overflow-y-auto rounded-lg border border-neutral-200 bg-white">
        <table className="w-full text-xs">
          <thead><tr className="border-b border-neutral-200 bg-neutral-50 sticky top-0">
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold w-10">#</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">Name</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">Email</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">Role</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">ERP Code</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">Department</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold">Teams Account</th>
            <th className="px-3 py-2 text-left text-neutral-500 font-semibold w-28">Status</th>
          </tr></thead>
          <tbody>
            {rows.map((row) => {
              const isDup = row.errors.length === 0 && existingEmails.has(row.email.toLowerCase())
              const isErr = row.errors.length > 0
              return (
                <tr key={row.line} className={cn('border-b border-neutral-100 last:border-0', isErr ? 'bg-danger-50/50' : isDup ? 'bg-warning-50/50' : '')}>
                  <td className="px-3 py-2 text-neutral-400">{row.line}</td>
                  <td className="px-3 py-2 text-neutral-700">{row.full_name || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2 text-neutral-600 font-mono">{row.email || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2 text-neutral-600">{row.role || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2 text-neutral-600 font-mono">{row.erp_person_code || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2 text-neutral-600">{row.department || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2 text-neutral-600 font-mono">{row.teams_account || <span className="text-neutral-300 italic">—</span>}</td>
                  <td className="px-3 py-2">
                    {isErr
                      ? <span className="text-danger-600 font-medium">{row.errors.join('; ')}</span>
                      : isDup
                        ? <span className="text-warning-600">Duplicate</span>
                        : <span className="text-success-600 font-medium flex items-center gap-1"><Check className="h-3 w-3" />Ready</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={valid.length === 0} onClick={() => onConfirm(valid)}>
          <Upload className="h-3.5 w-3.5" />Import {valid.length} User{valid.length !== 1 ? 's' : ''}
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

// ─── User Management ───────────────────────────────────────────────────────────

function UserManagement() {
  const rolesQ = useRoles()
  // Roles a user may hold as their PRIMARY role — backs both the CSV import's
  // validation and the downloadable template, so an additional-only role can't
  // sneak in through a hand-edited CSV either.
  const primaryRoleCodes = Object.keys(primaryRoleLabels(rolesQ.data))
  const { data: userData } = useUsers()
  const users = userData?.items ?? []
  const createUser = useCreateUser()
  const updateUserMutation = useUpdateUser()
  const deleteUserMutation = useDeleteUser()
  const { data: deptData } = useDepartments()
  const departments = deptData?.items ?? []
  const [search, setSearch] = useState('')
  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [resetTarget, setResetTarget] = useState<string | null>(null)
  const [tempPwd, setTempPwd] = useState('')
  const [showTempPwd, setShowTempPwd] = useState(false)
  const [resetDone, setResetDone] = useState<string | null>(null)
  const [importRows, setImportRows] = useState<ImportRow[] | null>(null)
  const [importDone, setImportDone] = useState<number | null>(null)
  const [showExportMenu, setShowExportMenu] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const importRef = useRef<HTMLInputElement>(null)

  const asSaveError = (err: unknown, fallback: string) =>
    err instanceof Error ? err.message : fallback

  const handleReset = () => {
    if (!resetTarget || !tempPwd.trim() || tempPwd.length < 8) return
    updateUserMutation.mutate({ id: resetTarget, body: { password: tempPwd } })
    setResetDone(resetTarget)
    setTimeout(() => { setResetTarget(null); setTempPwd(''); setResetDone(null); setShowTempPwd(false) }, 1800)
  }

  const resetTargetUser = resetTarget ? users.find((u) => u.id === resetTarget) : null

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const text = ev.target?.result as string
      const rows = parseUserCsv(text, new Set(primaryRoleCodes))
      setImportRows(rows.length > 0 ? rows : null)
      if (rows.length === 0) alert('No data rows found. Check that the file has a header row and at least one data row.')
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  const handleImportConfirm = (valid: ImportRow[]) => {
    for (const row of valid) {
      const dept = departments.find((d) => d.name.toLowerCase() === row.department.toLowerCase())
      createUser.mutate({
        full_name: row.full_name, email: row.email,
        role: row.role as ApiUserRole,
        erp_person_code: row.erp_person_code,
        department_id: dept?.id ?? undefined,
        is_active: csvIsActive(row.is_active),
        password: INITIAL_PASSWORD,
        teams_account: row.teams_account || null,
      })
    }
    setImportRows(null)
    setImportDone(valid.length)
    setTimeout(() => setImportDone(null), 3000)
  }

  const filtered = users.filter((u) => {
    const q = search.toLowerCase()
    return !q || u.full_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q) || (ROLE_LABELS[u.role as UserRole] ?? '').toLowerCase().includes(q)
  })
  const editingUser = typeof mode === 'object' ? users.find((u) => u.id === mode.edit) : null
  const existingEmails = new Set(users.map((u) => u.email.toLowerCase()))

  return (
    <>
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="relative flex-1 min-w-52 max-w-xs">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <Input placeholder="Search users…" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <div className="flex items-center gap-2">
          {/* Export dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowExportMenu((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
            >
              <Download className="h-4 w-4" />Export<ChevronDown className="h-3.5 w-3.5 text-neutral-400" />
            </button>
            {showExportMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowExportMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-20 w-52 rounded-xl border border-neutral-200 bg-white shadow-lg overflow-hidden">
                  <button className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
                    onClick={() => { exportUsersCsv(users as ApiUser[]); setShowExportMenu(false) }}>
                    <Download className="h-4 w-4 text-neutral-400" />
                    Export all users (.csv)
                  </button>
                  <button className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
                    onClick={() => { downloadTemplate(primaryRoleCodes); setShowExportMenu(false) }}>
                    <FileText className="h-4 w-4 text-neutral-400" />
                    Download import template
                  </button>
                </div>
              </>
            )}
          </div>
          {/* Import */}
          <button
            onClick={() => importRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
          >
            <Upload className="h-4 w-4" />Import CSV
          </button>
          <input ref={importRef} type="file" accept=".csv,text/csv" className="hidden" onChange={handleImportFile} />
          <Button onClick={() => { setMode('add'); setSaveError(null) }} disabled={mode !== 'none'}><Plus className="h-4 w-4" />Add User</Button>
        </div>
      </div>

      {/* Import success banner */}
      {importDone !== null && (
        <div className="flex items-center gap-2 rounded-lg border border-success-200 bg-success-50 px-4 py-2.5 text-sm text-success-700">
          <Check className="h-4 w-4 shrink-0" />
          {importDone} user{importDone !== 1 ? 's' : ''} imported successfully. All accounts set to temporary password "{INITIAL_PASSWORD}" — users must change on first login.
        </div>
      )}

      {/* Import preview */}
      {importRows && (
        <ImportPanel
          rows={importRows}
          existingEmails={existingEmails}
          onConfirm={handleImportConfirm}
          onCancel={() => setImportRows(null)}
        />
      )}

      {mode === 'add' && <UserForm title="Add New User" initial={BLANK_USER} saveError={saveError}
        onSave={(d) => {
          setSaveError(null)
          createUser.mutate(
            { full_name: d.full_name, email: d.email, role: d.role as ApiUserRole, erp_person_code: d.erp_person_code.trim(), department_id: d.department_id || undefined, is_active: d.is_active, password: INITIAL_PASSWORD, teams_account: d.teams_account || null },
            { onSuccess: () => setMode('none'), onError: (err) => setSaveError(asSaveError(err, 'Failed to create user')) },
          )
        }}
        onCancel={() => { setMode('none'); setSaveError(null) }} />}
      {editingUser && <UserForm title={`Edit — ${editingUser.full_name}`} saveError={saveError}
        initial={{ full_name: editingUser.full_name, email: editingUser.email, role: editingUser.role as UserRole, erp_person_code: editingUser.erp_person_code ?? '', department_id: editingUser.department_id ?? '', is_active: editingUser.is_active, teams_account: editingUser.teams_account ?? '', supervisor_id: editingUser.supervisor_id ?? '' }}
        onSave={(d) => {
          setSaveError(null)
          updateUserMutation.mutate(
            { id: (mode as { edit: string }).edit, body: { full_name: d.full_name, email: d.email, role: d.role as ApiUserRole, erp_person_code: d.erp_person_code.trim(), department_id: d.department_id || undefined, is_active: d.is_active, teams_account: d.teams_account || null, supervisor_id: d.supervisor_id || null } },
            { onSuccess: () => setMode('none'), onError: (err) => setSaveError(asSaveError(err, 'Failed to update user')) },
          )
        }}
        onCancel={() => { setMode('none'); setSaveError(null) }} />}

      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-neutral-200 bg-neutral-50">
              {['Name', 'Email', 'Role', 'Department', 'Teams Account', 'Status', ''].map((h, i) => (
                <th key={i} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {filtered.length === 0 && <tr><td colSpan={7} className="py-16 text-center text-sm text-neutral-400">No users found</td></tr>}
              {filtered.map((user, i) => (
                <tr key={user.id} className={cn('border-b border-neutral-100 hover:bg-primary-50 transition-colors', i % 2 === 1 ? 'bg-neutral-50' : 'bg-white')}>
                  <td className="px-4 py-3 font-medium text-neutral-900">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-100 text-xs font-semibold text-primary-700">{user.full_name.split(' ').map((n) => n[0]).join('').slice(0, 2).toUpperCase()}</div>
                      {user.full_name}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-neutral-600">{user.email}</td>
                  <td className="px-4 py-3 text-neutral-700">{ROLE_LABELS[user.role as UserRole]}</td>
                  <td className="px-4 py-3 text-neutral-600">{user.department_name || <span className="text-neutral-300">—</span>}</td>
                  <td className="px-4 py-3 text-neutral-600 text-xs">{user.teams_account || <span className="text-neutral-300">—</span>}</td>
                  <td className="px-4 py-3"><span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', user.is_active ? 'bg-success-50 text-success-700' : 'bg-neutral-100 text-neutral-500')}>{user.is_active ? '● Active' : '○ Inactive'}</span></td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {deleteConfirm === user.id ? (
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-danger-600 font-medium whitespace-nowrap">
                            {user.is_active ? 'Deactivate?' : 'Reactivate?'}
                          </span>
                          <button onClick={() => {
                            if (user.is_active) {
                              deleteUserMutation.mutate(user.id)
                            } else {
                              // Reactivate: use update mutation
                              updateUserMutation.mutate({ id: user.id, body: { is_active: true } })
                            }
                            setDeleteConfirm(null)
                          }} className="flex h-7 w-7 items-center justify-center rounded text-danger-600 hover:bg-danger-50"><Check className="h-3.5 w-3.5" /></button>
                          <button onClick={() => setDeleteConfirm(null)} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"><X className="h-3.5 w-3.5" /></button>
                        </div>
                      ) : (
                        <><button type="button" onClick={() => { setResetTarget(user.id); setTempPwd(INITIAL_PASSWORD); setShowTempPwd(false); setResetDone(null) }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-warning-50 hover:text-warning-600" title="Reset password"><KeyRound className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => { setMode({ edit: user.id }); setDeleteConfirm(null); setResetTarget(null) }} className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"><Pencil className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => { setDeleteConfirm(user.id); setMode('none'); setResetTarget(null) }}
                            className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-danger-50 hover:text-danger-500"
                            title={user.is_active ? 'Deactivate user' : 'Reactivate user'}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </button></>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-2.5 text-xs text-neutral-500">{filtered.length} of {users.length} user{users.length !== 1 ? 's' : ''}</div>
      </div>
    </div>

    {/* Reset Password Modal */}
    {resetTargetUser && createPortal(
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
        <div className="w-full max-w-sm rounded-2xl bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-warning-50">
                <KeyRound className="h-5 w-5 text-warning-600" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-neutral-900">Reset Password</h2>
                <p className="text-xs text-neutral-500">{resetTargetUser.full_name} · {resetTargetUser.email}</p>
              </div>
            </div>
            <button type="button" onClick={() => { setResetTarget(null); setTempPwd(''); setShowTempPwd(false); setResetDone(null) }} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="px-6 py-5 flex flex-col gap-4">
            {resetDone ? (
              <p className="flex items-center gap-2 text-sm text-success-700 font-medium">
                <Check className="h-4 w-4" />Password reset. User must change on next login.
              </p>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-neutral-700">New Temporary Password</label>
                  <div className="relative">
                    <input
                      type={showTempPwd ? 'text' : 'password'}
                      value={tempPwd}
                      onChange={(e) => setTempPwd(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleReset() }}
                      placeholder="Min. 8 characters"
                      autoComplete="new-password"
                      autoFocus
                      className="h-10 w-full rounded-lg border border-neutral-300 bg-white px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    />
                    <button type="button" onClick={() => setShowTempPwd((v) => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                      {showTempPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                  <p className="text-xs text-neutral-400">User will be required to change password on next login.</p>
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="secondary" size="sm" onClick={() => { setResetTarget(null); setTempPwd(''); setShowTempPwd(false) }}>Cancel</Button>
                  <button
                    type="button"
                    disabled={tempPwd.length < 8}
                    onClick={handleReset}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-warning-500 px-3 text-sm font-medium text-white hover:bg-warning-600 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <KeyRound className="h-3.5 w-3.5" />Reset Password
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>,
      document.body
    )}
    </>
  )
}

// ─── Service GR SLA ───────────────────────────────────────────────────────────

const DEFAULT_SERVICE_GR_SLA: ServiceGrSlaConfig = { reminder_days: 1, manager_escalation_days: 3, gm_opm_escalation_days: 5, fm_alert_days: 7 }

function ServiceGrSla() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [cfg, setCfg] = useState<ServiceGrSlaConfig>(DEFAULT_SERVICE_GR_SLA)
  const [saved, setSaved] = useState(false)
  const set = <K extends keyof ServiceGrSlaConfig>(k: K, v: number) => setCfg((p) => ({ ...p, [k]: v }))

  useEffect(() => { if (config?.service_gr_sla) setCfg(config.service_gr_sla) }, [config?.service_gr_sla])

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <p className="text-sm text-neutral-500">
        Configures the SLA escalation ladder for <strong>Service Receipt Confirmation</strong> (Type 4 / service lines of Type 6 POs).
        Day 0 is always the Service Expected Completion Date set on the PO.
      </p>

      {/* Ladder diagram */}
      <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4 font-mono text-xs text-neutral-600 leading-6">
        <p>Day 0        → Task created for Requester</p>
        <p>Day 0 + {String(cfg.reminder_days).padEnd(2)}  → Reminder to Requester</p>
        <p>Day 0 + {String(cfg.manager_escalation_days).padEnd(2)}  → Escalation to Dept. Manager</p>
        <p>Day 0 + {String(cfg.gm_opm_escalation_days).padEnd(2)}  → Escalation to GM / OPM</p>
        <p>Day 0 + {String(cfg.fm_alert_days).padEnd(2)}  → Finance Manager alert</p>
      </div>

      <div className="flex flex-col gap-3">
        <SlaRow label="Requester reminder" description="First reminder sent to Requester after Day 0." value={cfg.reminder_days} onChange={(v) => set('reminder_days', v)} />
        <SlaRow label="Dept. Manager escalation" description="Escalate to Dept. Manager if Requester has not confirmed." value={cfg.manager_escalation_days} onChange={(v) => set('manager_escalation_days', v)} min={cfg.reminder_days + 1} />
        <SlaRow label="GM / OPM escalation" description="Escalate to GM or OPM. PO flagged as overdue on dashboard." value={cfg.gm_opm_escalation_days} onChange={(v) => set('gm_opm_escalation_days', v)} min={cfg.manager_escalation_days + 1} />
        <SlaRow label="Finance Manager alert" description="Finance Manager notified. PO flagged 'Confirmation Overdue'." value={cfg.fm_alert_days} onChange={(v) => set('fm_alert_days', v)} min={cfg.gm_opm_escalation_days + 1} />
      </div>

      <SaveBar saved={saved} onSave={() => { updateConfig.mutate({ service_gr_sla: cfg }); setSaved(true); setTimeout(() => setSaved(false), 2500) }} />
    </div>
  )
}

// ─── GR Notification SLA ─────────────────────────────────────────────────────

function GrNotificationSla() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [cfg, setCfg] = useState<GrNotificationSlaConfig>({ reminder_days: 1, manager_escalation_days: 3 })
  const [saved, setSaved] = useState(false)

  useEffect(() => { if (config?.gr_notification_sla) setCfg(config.gr_notification_sla) }, [config?.gr_notification_sla])

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <p className="text-sm text-neutral-500">
        Configures reminders and escalation for <strong>GR Acknowledgement</strong>.
        When Warehouse Staff save a Physical GR, the Requester must click "Acknowledge Receipt".
        These timers control follow-up if they don't.
      </p>
      <div className="flex flex-col gap-3">
        <SlaRow label="Requester acknowledgement reminder" description="Reminder sent to Requester if GR has not been acknowledged." value={cfg.reminder_days} onChange={(v) => setCfg((p) => ({ ...p, reminder_days: v }))} />
        <SlaRow label="Dept. Manager escalation for non-acknowledgement" description="Dept. Manager notified: '[Requester] has not acknowledged GR [#]. Please follow up.'" value={cfg.manager_escalation_days} onChange={(v) => setCfg((p) => ({ ...p, manager_escalation_days: v }))} min={cfg.reminder_days + 1} />
      </div>
      <div className="rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-3 text-xs text-neutral-600">
        <p className="font-medium mb-0.5">Note: thresholds are configurable per department</p>
        <p>These values set the global default. Per-department overrides can be added in the department configuration (future release).</p>
      </div>
      <SaveBar saved={saved} onSave={() => { updateConfig.mutate({ gr_notification_sla: cfg }); setSaved(true); setTimeout(() => setSaved(false), 2500) }} />
    </div>
  )
}

// ─── Prepayment Config ────────────────────────────────────────────────────────

const DEFAULT_PREPAYMENT_CONFIG: PrepaymentConfig = {
  max_prepayment_pct: 100,
  settlement_sla_days: 5,
  settlement_manager_escalation_days: 3,
  settlement_gm_opm_escalation_days: 5,
  block_po_closure_on_unsettled: true,
}

// ─── Vendor Settings ──────────────────────────────────────────────────────────

function VendorSettingsSection() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [categories, setCategories] = useState<string[]>([])
  const [newCat, setNewCat] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (config?.vendor_categories) setCategories(config.vendor_categories)
  }, [config?.vendor_categories])

  const addCategory = () => {
    const v = newCat.trim()
    if (!v || categories.includes(v)) return
    setCategories((prev) => [...prev, v])
    setNewCat('')
  }

  const removeCategory = (cat: string) => setCategories((prev) => prev.filter((c) => c !== cat))

  const handleSave = () => {
    updateConfig.mutate({ vendor_categories: categories })
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Vendor Categories</h3>
        <p className="text-sm text-neutral-500">These categories appear in the dropdown when adding or editing a vendor.</p>

        {/* Existing categories */}
        <div className="flex flex-col gap-2">
          {categories.length === 0 && (
            <p className="text-sm text-neutral-400 italic">No categories defined.</p>
          )}
          {categories.map((cat) => (
            <div key={cat} className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-2.5">
              <span className="text-sm text-neutral-800">{cat}</span>
              <button
                type="button"
                onClick={() => removeCategory(cat)}
                className="text-neutral-400 hover:text-danger-600 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>

        {/* Add new */}
        <div className="flex gap-2">
          <input
            className={inputCls()}
            placeholder="New category name…"
            value={newCat}
            onChange={(e) => setNewCat(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCategory() } }}
          />
          <Button type="button" variant="secondary" onClick={addCategory} disabled={!newCat.trim()}>
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </div>
      </section>

      <SaveBar saved={saved} onSave={handleSave} />
    </div>
  )
}

function PrepaymentConfigSection() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [cfg, setCfg] = useState<PrepaymentConfig>(DEFAULT_PREPAYMENT_CONFIG)
  const [saved, setSaved] = useState(false)
  const set = <K extends keyof PrepaymentConfig>(k: K, v: PrepaymentConfig[K]) => setCfg((p) => ({ ...p, [k]: v }))

  useEffect(() => { if (config?.prepayment_config) setCfg(config.prepayment_config) }, [config?.prepayment_config])

  const numCls = 'h-10 w-24 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 text-center'

  return (
    <div className="flex flex-col gap-8 max-w-lg">
      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Prepayment Limits</h3>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-4 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-neutral-900">Maximum Prepayment Percentage</p>
            <p className="text-xs text-neutral-500 mt-0.5">Global cap. Requester cannot request a prepayment above this % of the PO value. Can be overridden per vendor category.</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <input type="number" min={1} max={100} value={cfg.max_prepayment_pct} onChange={(e) => set('max_prepayment_pct', Math.min(100, Math.max(1, Number(e.target.value))))} className={numCls} />
            <span className="text-sm text-neutral-500">%</span>
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Settlement SLA</h3>
        <p className="text-sm text-neutral-500">After GR is confirmed for a prepayment PO, the Requester must settle the prepayment within this window.</p>
        <SlaRow label="Settlement deadline" description="Days after GR confirmation before settlement is overdue." value={cfg.settlement_sla_days} onChange={(v) => set('settlement_sla_days', v)} />
        <SlaRow label="Dept. Manager escalation (after breach)" description="Days after SLA breach before Dept. Manager is escalated." value={cfg.settlement_manager_escalation_days} onChange={(v) => set('settlement_manager_escalation_days', v)} />
        <SlaRow label="GM / OPM escalation (after breach)" description="Days after SLA breach before GM or OPM is escalated." value={cfg.settlement_gm_opm_escalation_days} onChange={(v) => set('settlement_gm_opm_escalation_days', v)} />
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">PO Closure</h3>
        <ToggleRow label="Block PO closure when prepayment is unsettled" description="Prevents Procurement Officers from closing a PO that has an outstanding prepayment not yet settled. Finance Manager can override per-instance." checked={cfg.block_po_closure_on_unsettled} onToggle={() => set('block_po_closure_on_unsettled', !cfg.block_po_closure_on_unsettled)} />
      </section>

      <SaveBar saved={saved} onSave={() => { updateConfig.mutate({ prepayment_config: cfg }); setSaved(true); setTimeout(() => setSaved(false), 2500) }} />
    </div>
  )
}


// ─── Collection Config ────────────────────────────────────────────────────────

const DEFAULT_COLLECTION_CONFIG: CollectionConfig = {
  collection_required: true,
  reminder_days: 2,
  manager_escalation_days: 5,
  fm_alert_days: 10,
}

function CollectionConfigSection() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [cfg, setCfg] = useState<CollectionConfig>(DEFAULT_COLLECTION_CONFIG)
  const [saved, setSaved] = useState(false)
  const set = <K extends keyof CollectionConfig>(k: K, v: CollectionConfig[K]) => setCfg((p) => ({ ...p, [k]: v }))

  useEffect(() => { if (config?.collection_config) setCfg(config.collection_config) }, [config?.collection_config])

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <p className="text-sm text-neutral-500">
        Controls the <strong>Physical Goods Collection Confirmation</strong> step (§6.3). After Warehouse Staff confirm a Physical GR,
        the Requester must physically collect the goods and record the collection in EPMS before a PA can be created.
      </p>

      <ToggleRow
        label="Collection Confirmation Required"
        description="When enabled, PA creation for physical GRs is blocked until the Requester submits a Collection Confirmation. When disabled, PA creation is unlocked on GR acknowledgement (legacy behaviour)."
        checked={cfg.collection_required}
        onToggle={() => set('collection_required', !cfg.collection_required)}
      />

      {cfg.collection_required ? (
        <div className="flex flex-col gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Collection SLA Escalation</h3>
          <p className="text-sm text-neutral-500">Timers start from the date the Physical GR is saved.</p>
          <SlaRow label="Requester reminder" description="Reminder: 'Goods are ready to collect at Technical Warehouse for PO [#].'" value={cfg.reminder_days} onChange={(v) => set('reminder_days', v)} />
          <SlaRow label="Dept. Manager escalation" description="Dept. Manager notified: 'Goods for PO [#] have not been collected. Please follow up.'" value={cfg.manager_escalation_days} onChange={(v) => set('manager_escalation_days', v)} min={cfg.reminder_days + 1} />
          <SlaRow label="Finance Manager alert" description="Finance Manager notified. GR flagged 'Collection Overdue' on dashboard." value={cfg.fm_alert_days} onChange={(v) => set('fm_alert_days', v)} min={cfg.manager_escalation_days + 1} />
        </div>
      ) : (
        <div className="rounded-lg bg-warning-50 border border-warning-200 px-4 py-3 text-xs text-warning-700">
          <p className="font-medium mb-0.5">Collection step is disabled</p>
          <p>PA creation for physical GRs is unlocked upon GR acknowledgement. Collection SLA timers are not active.</p>
        </div>
      )}

      <SaveBar saved={saved} onSave={() => { updateConfig.mutate({ collection_config: cfg }); setSaved(true); setTimeout(() => setSaved(false), 2500) }} />
    </div>
  )
}

// ─── Notification Settings ────────────────────────────────────────────────────

const NOTIF_CHANNEL_OPTIONS = [
  { value: 'email_only', label: 'Email only' },
  { value: 'teams_only', label: 'Teams only' },
  { value: 'both', label: 'Email + Teams' },
  { value: 'none', label: 'None (in-app only)' },
]

const EMAIL_TEMPLATE_LABELS: Record<string, string> = {
  pr_approval_request: 'PR — Approval Request',
  pr_returned: 'PR — Returned for Revision',
  pr_rejected: 'PR — Rejected',
  pr_approved: 'PR — Approved',
  po_approval_request: 'PO — Approval Request',
  gr_created: 'GR — Goods Received',
  gr_collection_ready: 'GR — Collection Ready',
  service_gr_pending: 'GR — Service Confirmation Pending',
  pa_approval_request: 'PA — Approval Request',
  pa_approved: 'PA — Approved',
  prepayment_settlement_overdue: 'PA — Prepayment Settlement Overdue',
  daily_pending_reminder: 'Daily Pending Tasks Reminder',
  sla_escalation: 'SLA Escalation Alert',
  match_invoice_assigned: 'Invoice — Matching Assigned to You',
  match_review_request: 'Invoice — Confirm Delegate Match',
  exception_resolution_request: 'Invoice — Outside Match Tolerance',
}

const DEFAULT_NOTIF_SETTINGS: NotificationSettings = {
  default_channel: 'email_only',
  teams_webhook_url: null,
  followup_time: '08:00',
}

function NotificationSettingsSection() {
  const { data: config } = useConfig()
  const updateConfig = useUpdateConfig()
  const [settings, setSettings] = useState<NotificationSettings>(DEFAULT_NOTIF_SETTINGS)
  const [templates, setTemplates] = useState<Record<string, EmailTemplate>>({})
  const [saved, setSaved] = useState(false)
  const [expandedTpl, setExpandedTpl] = useState<string | null>(null)
  const [testEmail, setTestEmail] = useState('')
  const [testStatus, setTestStatus] = useState<'idle' | 'sending' | 'ok' | 'error'>('idle')

  useEffect(() => {
    if (config?.notification_settings) setSettings(config.notification_settings as NotificationSettings)
    if (config?.email_templates) setTemplates(config.email_templates as Record<string, EmailTemplate>)
  }, [config?.notification_settings, config?.email_templates])

  const handleSave = () => {
    updateConfig.mutate({ notification_settings: settings, email_templates: templates })
    setSaved(true); setTimeout(() => setSaved(false), 2500)
  }

  const handleTestSmtp = async () => {
    if (!testEmail) return
    setTestStatus('sending')
    try {
      await configService.testSmtp(testEmail)
      setTestStatus('ok'); setTimeout(() => setTestStatus('idle'), 3000)
    } catch {
      setTestStatus('error'); setTimeout(() => setTestStatus('idle'), 3000)
    }
  }

  const updateTemplate = (key: string, field: 'subject' | 'body', value: string) => {
    setTemplates((p) => ({ ...p, [key]: { ...(p[key] ?? { subject: '', body: '' }), [field]: value } }))
  }

  const inCls = 'h-10 w-full rounded-lg border border-neutral-200 bg-neutral-100 px-3 text-sm focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors'

  return (
    <div className="flex flex-col gap-8 max-w-2xl">

      {/* Default channel */}
      <section className="flex flex-col gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Default Notification Channel</h3>
        <p className="text-sm text-neutral-500">Applied to all users who have not set a personal preference.</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {NOTIF_CHANNEL_OPTIONS.map(({ value, label }) => (
            <button key={value} type="button"
              onClick={() => setSettings((p) => ({ ...p, default_channel: value as NotificationSettings['default_channel'] }))}
              className={cn('rounded-lg border px-3 py-2.5 text-xs font-medium text-left transition-colors',
                settings.default_channel === value
                  ? 'border-primary-600 bg-primary-50 text-primary-700'
                  : 'border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>
      </section>

      {/* Teams webhook */}
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Microsoft Teams Webhook URL</h3>
        <p className="text-sm text-neutral-500">Company-level Incoming Webhook URL. Used when channel is "Teams only" or "Both".</p>
        <input className={inCls} placeholder="https://outlook.office.com/webhook/…"
          value={settings.teams_webhook_url ?? ''}
          onChange={(e) => setSettings((p) => ({ ...p, teams_webhook_url: e.target.value || null }))} />
      </section>

      {/* Follow-up time */}
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Daily Follow-up Time (UTC)</h3>
        <p className="text-sm text-neutral-500">Time at which the daily pending-task reminder is dispatched.</p>
        <input type="time" className={cn(inCls, 'w-36')}
          value={settings.followup_time ?? '08:00'}
          onChange={(e) => setSettings((p) => ({ ...p, followup_time: e.target.value }))} />
      </section>

      {/* Test SMTP */}
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Test Notification</h3>
        <p className="text-sm text-neutral-500">Send a test email using the current SMTP configuration.</p>
        <div className="flex gap-2">
          <input className={cn(inCls, 'flex-1')} type="email" placeholder="recipient@example.com"
            value={testEmail} onChange={(e) => setTestEmail(e.target.value)} />
          <button onClick={handleTestSmtp} disabled={testStatus === 'sending' || !testEmail}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50 shrink-0">
            <Send className="h-4 w-4" />
            {testStatus === 'sending' ? 'Sending…' : 'Send Test'}
          </button>
        </div>
        {testStatus === 'ok' && <p className="text-xs text-success-600">Test email sent successfully.</p>}
        {testStatus === 'error' && <p className="text-xs text-danger-600">Failed to send. Check SMTP settings.</p>}
      </section>

      <SaveBar saved={saved} onSave={handleSave} label="Save Notification Settings" />

      {/* Email template editor */}
      <section className="flex flex-col gap-3 pt-6 border-t border-neutral-200">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Email Templates</h3>
          <p className="text-sm text-neutral-500 mt-1">Customise the subject and body for each notification event. Use <code className="text-xs bg-neutral-100 px-1 rounded">{'{variable}'}</code> placeholders.</p>
        </div>
        <div className="flex flex-col gap-2">
          {Object.entries(EMAIL_TEMPLATE_LABELS).map(([key, label]) => {
            const tpl = templates[key] ?? { subject: '', body: '' }
            const isOpen = expandedTpl === key
            return (
              <div key={key} className="rounded-xl border border-neutral-200 overflow-hidden">
                <button type="button" onClick={() => setExpandedTpl(isOpen ? null : key)}
                  className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-neutral-800 hover:bg-neutral-50 text-left">
                  <span>{label}</span>
                  <ChevronDown className={cn('h-4 w-4 text-neutral-400 transition-transform', isOpen && 'rotate-180')} />
                </button>
                {isOpen && (
                  <div className="border-t border-neutral-100 px-4 pb-4 pt-3 flex flex-col gap-3 bg-neutral-50">
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">Subject</label>
                      <input className={inCls} value={tpl.subject}
                        onChange={(e) => updateTemplate(key, 'subject', e.target.value)}
                        placeholder="Email subject…" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">Body</label>
                      <textarea rows={6}
                        className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors resize-y"
                        value={tpl.body}
                        onChange={(e) => updateTemplate(key, 'body', e.target.value)}
                        placeholder="Email body (HTML or plain text)…" />
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
        <SaveBar saved={saved} onSave={handleSave} label="Save Templates" />
      </section>
    </div>
  )
}


// ─── Main Admin Panel page ────────────────────────────────────────────────────

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'


function MovedToPortal({
  section, path = '/admin', linkLabel = 'Open Portal Admin Panel', description,
}: {
  section: string
  path?: string
  linkLabel?: string
  description?: string
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-primary-200 bg-primary-50 p-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-100 mb-4">
        <svg className="h-6 w-6 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 7l5 5m0 0l-5 5m5-5H6" />
        </svg>
      </div>
      <h3 className="text-base font-semibold text-primary-800">{section} has moved</h3>
      <p className="mt-1.5 text-sm text-primary-600 max-w-sm">
        {description ?? 'This setting is now managed in the UniOps Portal Admin Panel as a global configuration shared across all modules.'}
      </p>
      <a
        href={`${PORTAL_URL}${path}`}
        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 transition-colors"
      >
        {linkLabel}
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
        </svg>
      </a>
    </div>
  )
}

export default function AdminPanel() {
  // Internal sub-nav via component state — kept out of the URL so it works under
  // the tab shell's keep-alive (which pins each tab to its own location).
  const [activeSection, setActiveSection] = useState<Section>('company')

  const current = NAV.find((n) => n.id === activeSection)!

  const renderSection = () => {
    switch (activeSection) {
      case 'company':             return <MovedToPortal section="Company Settings" />
      case 'security':            return <MovedToPortal section="Security" />
      case 'currency':            return <MovedToPortal section="Currency Settings" />
      case 'email_templates':     return <EmailTemplates />
      case 'pdf_templates':       return <PdfTemplates />
      case 'users':               return <MovedToPortal section="User Management" />
      case 'workflows':           return <MovedToPortal section="Approval Workflows" />
      case 'service_gr_sla':        return <ServiceGrSla />
      case 'gr_notification_sla': return <GrNotificationSla />
      case 'vendor_settings':     return <VendorSettingsSection />
      case 'prepayment':          return <PrepaymentConfigSection />
      case 'budget':              return (
        <MovedToPortal
          section="Budget Config"
          path="/budget/config"
          linkLabel="Open Budget Config"
          description="Budget configuration (fiscal year, alert thresholds, over-budget approval mode, and factor decomposition limits) is now managed in the UniOps Portal under FINANCE → Budget Config."
        />
      )
      case 'collection':          return <CollectionConfigSection />
      case 'notifications':       return <MovedToPortal section="Notification Settings" />
      case 'pms_import':          return <PmsImportPanel />
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-neutral-900">Admin Panel</h1>
        <p className="mt-1 text-sm text-neutral-500">System configuration and user management</p>
      </div>
      <div className="flex gap-6 items-start">
        <nav className="w-56 shrink-0 rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
          {NAV.map((entry) => {
            const Icon = entry.icon
            return (
              <button key={entry.id} onClick={() => setActiveSection(entry.id)}
                className={cn('w-full flex items-center gap-3 px-4 py-3 text-sm transition-colors text-left border-b border-neutral-100 last:border-0',
                  activeSection === entry.id ? 'bg-primary-50 text-primary-700 font-medium border-l-2 border-l-primary-600 pl-[14px]' : 'text-neutral-700 hover:bg-neutral-50')}>
                <Icon className={cn('h-4 w-4 shrink-0', activeSection === entry.id ? 'text-primary-600' : 'text-neutral-400')} />
                {entry.label}
              </button>
            )
          })}
        </nav>
        <div className="flex-1 min-w-0 rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
          <h2 className="text-base font-semibold text-neutral-900 mb-5">{current.label}</h2>
          {renderSection()}
        </div>
      </div>
    </div>
  )
}
