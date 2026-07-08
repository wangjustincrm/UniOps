/**
 * SettingsPage — /admin/settings
 *
 * Two cards:
 *   1. Booking Rules: slot_minutes, min/max_duration_minutes, advance_days,
 *      default_open_start/end, notify_room_admin toggle, room_admin_emails
 *      (comma-separated text input)
 *   2. Email (SMTP): host, port, user, password (masked), use_tls,
 *      from_email, organizer_mode select
 *
 * Save buttons per card → PUT /admin/config with partial payload.
 */
import { useState, useEffect } from 'react'
import { Loader2, AlertCircle, CheckCircle2, X as XIcon, Eye, EyeOff } from 'lucide-react'
import { useAdminConfig, useAdminUpdateConfig } from '@/services/api'
import type { BookingRules, SmtpSettings } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-neutral-700 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-0.5 text-xs text-neutral-400">{hint}</p>}
    </div>
  )
}

const inputCls =
  'w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40'

// ── Rules card ────────────────────────────────────────────────────────────────

interface RulesCardProps {
  rules: BookingRules
  onSave: (rules: BookingRules) => Promise<void>
  saving: boolean
}

function RulesCard({ rules: initialRules, onSave, saving }: RulesCardProps) {
  const [form, setForm] = useState<BookingRules>(initialRules)
  const [emailsRaw, setEmailsRaw] = useState(
    (initialRules.room_admin_emails ?? []).join(', '),
  )
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setForm(initialRules)
    setEmailsRaw((initialRules.room_admin_emails ?? []).join(', '))
  }, [initialRules])

  function setNum(key: keyof BookingRules, val: string) {
    setForm((f) => ({ ...f, [key]: val === '' ? null : Number(val) }))
  }

  function setStr(key: keyof BookingRules, val: string) {
    setForm((f) => ({ ...f, [key]: val || null }))
  }

  async function handleSave() {
    setSuccess(false)
    setError(null)
    const emails = emailsRaw
      .split(',')
      .map((e) => e.trim())
      .filter(Boolean)
    try {
      await onSave({ ...form, room_admin_emails: emails })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white shadow-sm">
      <div className="px-6 py-4 border-b border-neutral-100">
        <h2 className="text-sm font-semibold text-neutral-900">Booking Rules</h2>
      </div>
      <div className="px-6 py-5 space-y-4">
        {success && (
          <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            <CheckCircle2 className="h-4 w-4" />
            Rules saved.
          </div>
        )}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
            <div className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{error}</div>
            <button type="button" onClick={() => setError(null)}><XIcon className="h-4 w-4" /></button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <Field label="Slot Minutes" hint="Meeting start/end granularity">
            <input
              type="number" min={5} max={60}
              value={form.slot_minutes ?? ''}
              onChange={(e) => setNum('slot_minutes', e.target.value)}
              className={inputCls}
              placeholder="30"
            />
          </Field>
          <Field label="Min Duration (minutes)">
            <input
              type="number" min={1}
              value={form.min_duration_minutes ?? ''}
              onChange={(e) => setNum('min_duration_minutes', e.target.value)}
              className={inputCls}
              placeholder="30"
            />
          </Field>
          <Field label="Max Duration (minutes)">
            <input
              type="number" min={1}
              value={form.max_duration_minutes ?? ''}
              onChange={(e) => setNum('max_duration_minutes', e.target.value)}
              className={inputCls}
              placeholder="480"
            />
          </Field>
          <Field label="Advance Booking Days" hint="How far ahead users can book">
            <input
              type="number" min={0}
              value={form.advance_days ?? ''}
              onChange={(e) => setNum('advance_days', e.target.value)}
              className={inputCls}
              placeholder="30"
            />
          </Field>
          <Field label="Default Open Start">
            <input
              type="time"
              value={form.default_open_start ?? ''}
              onChange={(e) => setStr('default_open_start', e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Default Open End">
            <input
              type="time"
              value={form.default_open_end ?? ''}
              onChange={(e) => setStr('default_open_end', e.target.value)}
              className={inputCls}
            />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          <input
            id="notify_room_admin"
            type="checkbox"
            checked={!!form.notify_room_admin}
            onChange={(e) => setForm((f) => ({ ...f, notify_room_admin: e.target.checked }))}
            className="h-4 w-4 rounded border-neutral-300 text-[#085E5E] focus:ring-[#085E5E]/30"
          />
          <label htmlFor="notify_room_admin" className="text-sm text-neutral-700">
            Notify room admin when a booking is made or cancelled
          </label>
        </div>

        {form.notify_room_admin && (
          <Field
            label="Room Admin Emails"
            hint="Comma-separated list of email addresses"
          >
            <input
              type="text"
              value={emailsRaw}
              onChange={(e) => setEmailsRaw(e.target.value)}
              className={inputCls}
              placeholder="admin@example.com, facilities@example.com"
            />
          </Field>
        )}
      </div>
      <div className="px-6 py-4 border-t border-neutral-100 flex justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-[#085E5E] px-4 py-1.5 text-sm font-semibold text-white hover:bg-[#085E5E]/90 disabled:opacity-50"
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save Rules
        </button>
      </div>
    </div>
  )
}

// ── SMTP card ─────────────────────────────────────────────────────────────────

interface SmtpCardProps {
  smtp: SmtpSettings
  organizerMode: 'system' | 'initiator'
  onSave: (smtp: SmtpSettings, mode: 'system' | 'initiator') => Promise<void>
  saving: boolean
}

function SmtpCard({ smtp: initialSmtp, organizerMode: initialMode, onSave, saving }: SmtpCardProps) {
  const [form, setForm] = useState<SmtpSettings>(initialSmtp)
  const [mode, setMode] = useState<'system' | 'initiator'>(initialMode)
  const [showPw, setShowPw] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setForm(initialSmtp)
    setMode(initialMode)
  }, [initialSmtp, initialMode])

  function setStr(key: keyof SmtpSettings, val: string) {
    setForm((f) => ({ ...f, [key]: val || null }))
  }

  async function handleSave() {
    setSuccess(false)
    setError(null)
    try {
      await onSave(form, mode)
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white shadow-sm">
      <div className="px-6 py-4 border-b border-neutral-100">
        <h2 className="text-sm font-semibold text-neutral-900">Email (SMTP)</h2>
      </div>
      <div className="px-6 py-5 space-y-4">
        {success && (
          <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            <CheckCircle2 className="h-4 w-4" />
            Settings saved.
          </div>
        )}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
            <div className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{error}</div>
            <button type="button" onClick={() => setError(null)}><XIcon className="h-4 w-4" /></button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div className="col-span-2 sm:col-span-1">
            <Field label="SMTP Host">
              <input
                type="text"
                value={form.host ?? ''}
                onChange={(e) => setStr('host', e.target.value)}
                className={inputCls}
                placeholder="smtp.example.com"
              />
            </Field>
          </div>
          <Field label="Port">
            <input
              type="number"
              value={form.port ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, port: e.target.value ? Number(e.target.value) : null }))}
              className={inputCls}
              placeholder="587"
            />
          </Field>
          <Field label="Username">
            <input
              type="text"
              value={form.user ?? ''}
              onChange={(e) => setStr('user', e.target.value)}
              className={inputCls}
              placeholder="user@example.com"
              autoComplete="off"
            />
          </Field>
          <Field label="Password">
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                value={form.password ?? ''}
                onChange={(e) => setStr('password', e.target.value)}
                className={inputCls + ' pr-9'}
                placeholder={form.password ? '••••••••' : 'Enter password'}
                autoComplete="new-password"
              />
              <button
                type="button"
                onClick={() => setShowPw((s) => !s)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
              >
                {showPw ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
          </Field>
          <Field label="From Email">
            <input
              type="email"
              value={form.from_email ?? ''}
              onChange={(e) => setStr('from_email', e.target.value)}
              className={inputCls}
              placeholder="booking@example.com"
            />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          <input
            id="use_tls"
            type="checkbox"
            checked={!!form.use_tls}
            onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))}
            className="h-4 w-4 rounded border-neutral-300 text-[#085E5E] focus:ring-[#085E5E]/30"
          />
          <label htmlFor="use_tls" className="text-sm text-neutral-700">Use TLS</label>
        </div>

        <Field
          label="Organizer Mode"
          hint={
            mode === 'system'
              ? 'System mailbox: invites sent from the system address.'
              : 'Meeting organizer: shows as sent on behalf of the organizer.'
          }
        >
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as 'system' | 'initiator')}
            className={inputCls}
          >
            <option value="system">System mailbox</option>
            <option value="initiator">Meeting organizer</option>
          </select>
        </Field>
      </div>
      <div className="px-6 py-4 border-t border-neutral-100 flex justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-[#085E5E] px-4 py-1.5 text-sm font-semibold text-white hover:bg-[#085E5E]/90 disabled:opacity-50"
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save Email Settings
        </button>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { data: config, isLoading, error } = useAdminConfig()
  const updateMut = useAdminUpdateConfig()

  async function handleRulesSave(rules: BookingRules) {
    await updateMut.mutateAsync({ rules })
  }

  async function handleSmtpSave(smtp: SmtpSettings, organizer_mode: 'system' | 'initiator') {
    await updateMut.mutateAsync({ smtp_settings: smtp, organizer_mode })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-neutral-500">
        <Loader2 className="h-5 w-5 animate-spin mr-2" />
        Loading settings…
      </div>
    )
  }

  if (error || !config) {
    return (
      <div className="flex items-center gap-2 m-6 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
        <AlertCircle className="h-4 w-4 shrink-0" />
        Failed to load settings. Please refresh.
      </div>
    )
  }

  const defaultRules: BookingRules = {
    slot_minutes: null,
    min_duration_minutes: null,
    max_duration_minutes: null,
    advance_days: null,
    default_open_start: null,
    default_open_end: null,
    notify_room_admin: null,
    room_admin_emails: [],
  }

  const defaultSmtp: SmtpSettings = {
    host: null,
    port: null,
    user: null,
    password: null,
    use_tls: null,
    from_email: null,
  }

  const rules: BookingRules = { ...defaultRules, ...(config.rules ?? {}) }
  const smtp: SmtpSettings = { ...defaultSmtp, ...(config.smtp_settings ?? {}) }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-6 sm:px-6 space-y-6">
        <h1 className="text-2xl font-bold text-neutral-900">Settings</h1>
        <RulesCard
          rules={rules}
          onSave={handleRulesSave}
          saving={updateMut.isPending}
        />
        <SmtpCard
          smtp={smtp}
          organizerMode={config.organizer_mode}
          onSave={handleSmtpSave}
          saving={updateMut.isPending}
        />
      </div>
    </div>
  )
}
