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

import { Checkbox, Input, Select, Button } from '@uniops/shell'
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
      <label className="block text-xs font-medium text-fg-secondary mb-1">{label}</label>
      {children}
      {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
    </div>
  )
}

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
    <div className="rounded-xl border border-line bg-surface shadow-sm">
      <div className="px-6 py-4 border-b border-line-subtle">
        <h2 className="text-sm font-semibold text-fg">Booking Rules</h2>
      </div>
      <div className="px-6 py-5 space-y-4">
        {success && (
          <div className="flex items-center gap-2 rounded-md border border-success-line bg-success-soft px-3 py-2 text-sm text-success-fg">
            <CheckCircle2 className="h-4 w-4" />
            Rules saved.
          </div>
        )}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
            <div className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{error}</div>
            <Button variant="subtle" size="icon-xs" aria-label="Clear" type="button" onClick={() => setError(null)}><XIcon className="h-4 w-4" /></Button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <Field label="Slot Minutes" hint="Meeting start/end granularity">
            <Input
              type="number" min={5} max={60}
              value={form.slot_minutes ?? ''}
              onChange={(e) => setNum('slot_minutes', e.target.value)}
              placeholder="30"
            />
          </Field>
          <Field label="Min Duration (minutes)">
            <Input
              type="number" min={1}
              value={form.min_duration_minutes ?? ''}
              onChange={(e) => setNum('min_duration_minutes', e.target.value)}
              placeholder="30"
            />
          </Field>
          <Field label="Max Duration (minutes)">
            <Input
              type="number" min={1}
              value={form.max_duration_minutes ?? ''}
              onChange={(e) => setNum('max_duration_minutes', e.target.value)}
              placeholder="480"
            />
          </Field>
          <Field label="Advance Booking Days" hint="How far ahead users can book">
            <Input
              type="number" min={0}
              value={form.advance_days ?? ''}
              onChange={(e) => setNum('advance_days', e.target.value)}
              placeholder="30"
            />
          </Field>
          <Field label="Default Open Start">
            <Input
              type="time"
              value={form.default_open_start ?? ''}
              onChange={(e) => setStr('default_open_start', e.target.value)}
            />
          </Field>
          <Field label="Default Open End">
            <Input
              type="time"
              value={form.default_open_end ?? ''}
              onChange={(e) => setStr('default_open_end', e.target.value)}
            />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          <Checkbox
            id="notify_room_admin"
           
            checked={!!form.notify_room_admin}
            onChange={(e) => setForm((f) => ({ ...f, notify_room_admin: e.target.checked }))}
           
          />
          <label htmlFor="notify_room_admin" className="text-sm text-fg-secondary">
            Notify room admin when a booking is made or cancelled
          </label>
        </div>

        {form.notify_room_admin && (
          <Field
            label="Room Admin Emails"
            hint="Comma-separated list of email addresses"
          >
            <Input
              type="text"
              value={emailsRaw}
              onChange={(e) => setEmailsRaw(e.target.value)}
              placeholder="admin@example.com, facilities@example.com"
            />
          </Field>
        )}
      </div>
      <div className="px-6 py-4 border-t border-line-subtle flex justify-end">
        <Button size="sm"
          type="button"
          onClick={handleSave}
          disabled={saving}
         
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save Rules
        </Button>
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
    <div className="rounded-xl border border-line bg-surface shadow-sm">
      <div className="px-6 py-4 border-b border-line-subtle">
        <h2 className="text-sm font-semibold text-fg">Email (SMTP)</h2>
      </div>
      <div className="px-6 py-5 space-y-4">
        {success && (
          <div className="flex items-center gap-2 rounded-md border border-success-line bg-success-soft px-3 py-2 text-sm text-success-fg">
            <CheckCircle2 className="h-4 w-4" />
            Settings saved.
          </div>
        )}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
            <div className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{error}</div>
            <Button variant="subtle" size="icon-xs" aria-label="Clear" type="button" onClick={() => setError(null)}><XIcon className="h-4 w-4" /></Button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div className="col-span-2 sm:col-span-1">
            <Field label="SMTP Host">
              <Input
                type="text"
                value={form.host ?? ''}
                onChange={(e) => setStr('host', e.target.value)}
                placeholder="smtp.example.com"
              />
            </Field>
          </div>
          <Field label="Port">
            <Input
              type="number"
              value={form.port ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, port: e.target.value ? Number(e.target.value) : null }))}
              placeholder="587"
            />
          </Field>
          <Field label="Username">
            <Input
              type="text"
              value={form.user ?? ''}
              onChange={(e) => setStr('user', e.target.value)}
              placeholder="user@example.com"
              autoComplete="off"
            />
          </Field>
          <Field label="Password">
            <div className="relative">
              <Input
                type={showPw ? 'text' : 'password'}
                value={form.password ?? ''}
                onChange={(e) => setStr('password', e.target.value)}
                className="pr-9"
                placeholder={form.password ? '••••••••' : 'Enter password'}
                autoComplete="new-password"
              />
              <Button variant="subtle" size="icon-xs"
                type="button"
                onClick={() => setShowPw((s) => !s)}
                className="absolute right-1.5 top-1/2 -translate-y-1/2"
              >
                {showPw ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </Field>
          <Field label="From Email">
            <Input
              type="email"
              value={form.from_email ?? ''}
              onChange={(e) => setStr('from_email', e.target.value)}
              placeholder="booking@example.com"
            />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          <Checkbox
            id="use_tls"
           
            checked={!!form.use_tls}
            onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))}
           
          />
          <label htmlFor="use_tls" className="text-sm text-fg-secondary">Use TLS</label>
        </div>

        <Field
          label="Organizer Mode"
          hint={
            mode === 'system'
              ? 'System mailbox: invites sent from the system address.'
              : 'Meeting organizer: shows as sent on behalf of the organizer.'
          }
        >
          <Select
            value={mode}
            onChange={(e) => setMode(e.target.value as 'system' | 'initiator')}
            className="w-full"
          >
            <option value="system">System mailbox</option>
            <option value="initiator">Meeting organizer</option>
          </Select>
        </Field>
      </div>
      <div className="px-6 py-4 border-t border-line-subtle flex justify-end">
        <Button size="sm"
          type="button"
          onClick={handleSave}
          disabled={saving}
         
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save Email Settings
        </Button>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { data: config, isLoading, error } = useAdminConfig()
  const rulesMut = useAdminUpdateConfig()
  const smtpMut = useAdminUpdateConfig()

  async function handleRulesSave(rules: BookingRules) {
    await rulesMut.mutateAsync({ rules })
  }

  async function handleSmtpSave(smtp: SmtpSettings, organizer_mode: 'system' | 'initiator') {
    await smtpMut.mutateAsync({ smtp_settings: smtp, organizer_mode })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-fg-muted">
        <Loader2 className="h-5 w-5 animate-spin mr-2" />
        Loading settings…
      </div>
    )
  }

  if (error || !config) {
    return (
      <div className="flex items-center gap-2 m-6 rounded-md border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
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
        <h1 className="text-2xl font-semibold text-fg">Settings</h1>
        <RulesCard
          rules={rules}
          onSave={handleRulesSave}
          saving={rulesMut.isPending}
        />
        <SmtpCard
          smtp={smtp}
          organizerMode={config.organizer_mode}
          onSave={handleSmtpSave}
          saving={smtpMut.isPending}
        />
      </div>
    </div>
  )
}
