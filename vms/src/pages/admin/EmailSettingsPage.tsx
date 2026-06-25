/** VMS outbound SMTP settings (V2.5 — Admin owns the VMS mailbox).
 *
 * Up to now VMS borrowed the SMTP creds from `company_config` (shared with
 * EPMS / OA). Operators wanted a separate mailbox just for VMS. This page
 * persists VMS-local creds; if any field is left blank, the backend falls
 * back to the shared config so existing deployments keep working.
 *
 * Password handling: the backend masks the saved password as `********`
 * on GET, and treats the literal `********` on PUT as "keep current".
 * So leaving the password field at its loaded value is safe; blanking it
 * clears the credential.
 */
import { useState } from 'react'
import {
  AlertCircle, CheckCircle2, Loader2, Send, Mail,
} from 'lucide-react'
import {
  useSendSmtpTest, useSetSmtpSettings, useSmtpSettings, type SmtpSettings,
} from '@/services/api'

type Draft = Partial<Record<keyof SmtpSettings, string | number | boolean | null>>

const EMPTY: SmtpSettings = {
  host: null, port: null, user: null, password: null, use_tls: null, from_email: null,
}

export default function EmailSettingsPage() {
  const { data, isLoading } = useSmtpSettings()
  const save = useSetSmtpSettings()
  const test = useSendSmtpTest()

  // Drafts overlay server data — null entry means "use loaded value".
  const [draft, setDraft] = useState<Draft>({})
  const [testTo, setTestTo] = useState('')

  const eff = { ...EMPTY, ...data, ...draft } as SmtpSettings

  const onPatch = <K extends keyof SmtpSettings>(key: K, value: SmtpSettings[K]) => {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  const onSave = () => {
    save.mutate(eff, { onSuccess: () => setDraft({}) })
  }

  const onTest = () => {
    if (!testTo.trim()) return
    test.mutate({ to_email: testTo.trim() })
  }

  const dirty = Object.keys(draft).length > 0

  return (
    <div className="max-w-2xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-neutral-900">Email settings</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            SMTP credentials VMS uses to send notifications (visit approval
            emails, training / PPE requests). Falls back to the shared EPMS /
            OA mail server when these fields are blank.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {save.error && (
        <p className="mt-2 flex items-center gap-1.5 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          <AlertCircle className="h-3.5 w-3.5" />
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-2 rounded-md bg-success-50 px-3 py-2 text-xs text-success-600">
          Saved.
        </p>
      )}

      <div className="mt-5 grid grid-cols-1 gap-3 rounded-lg border border-neutral-200 bg-white p-4 md:grid-cols-2">
        <TextField
          label="SMTP host"
          value={eff.host ?? ''}
          onChange={(v) => onPatch('host', v.trim() || null)}
          loading={isLoading}
          placeholder="e.g. smtp.gmail.com"
        />
        <NumberField
          label="Port"
          value={eff.port}
          onChange={(v) => onPatch('port', v)}
          loading={isLoading}
          placeholder="587"
        />
        <TextField
          label="Username"
          value={eff.user ?? ''}
          onChange={(v) => onPatch('user', v.trim() || null)}
          loading={isLoading}
          placeholder="vms@royalmilk.com"
        />
        <TextField
          label="Password"
          type="password"
          value={eff.password ?? ''}
          onChange={(v) => onPatch('password', v)}
          loading={isLoading}
          placeholder="(leave masked to keep existing)"
        />
        <TextField
          label="From address"
          type="email"
          value={eff.from_email ?? ''}
          onChange={(v) => onPatch('from_email', v.trim() || null)}
          loading={isLoading}
          placeholder="vms@royalmilk.com"
        />
        <BoolField
          label="Use STARTTLS"
          value={eff.use_tls ?? false}
          onChange={(v) => onPatch('use_tls', v)}
          loading={isLoading}
        />
      </div>

      {/* Send test email */}
      <div className="mt-6 rounded-lg border border-neutral-200 bg-white p-4">
        <p className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
          <Mail className="h-4 w-4" />
          Send a test email
        </p>
        <p className="mt-0.5 text-xs text-neutral-500">
          Uses the currently-saved settings. Save first if you've made edits.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-sm">
            <span className="block text-xs text-neutral-600">Recipient</span>
            <input
              type="email"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              placeholder="you@example.com"
              className="mt-0.5 rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
          </label>
          <button
            type="button"
            onClick={onTest}
            disabled={!testTo.trim() || test.isPending}
            className="inline-flex items-center gap-1.5 rounded-md border border-primary-600 px-3 py-1.5 text-sm font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50"
          >
            {test.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            Send test
          </button>
        </div>
        {test.data && (
          <p className={
            'mt-3 rounded-md px-3 py-2 text-xs ' +
            (test.data.delivered ? 'bg-success-50 text-success-600' : 'bg-amber-50 text-amber-800')
          }>
            {test.data.detail}
          </p>
        )}
        {test.error && (
          <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
            {test.error.message}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Inputs ──────────────────────────────────────────────────────────────────-

function TextField({
  label, value, onChange, loading, placeholder, type = 'text',
}: {
  label: string; value: string
  onChange: (v: string) => void
  loading: boolean; placeholder: string
  type?: 'text' | 'email' | 'password'
}) {
  return (
    <label className="block text-sm">
      <span className="block font-medium text-neutral-700">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading}
        placeholder={placeholder}
        autoComplete={type === 'password' ? 'new-password' : 'off'}
        className="mt-1 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 disabled:opacity-50"
      />
    </label>
  )
}

function NumberField({
  label, value, onChange, loading, placeholder,
}: {
  label: string; value: number | null
  onChange: (v: number | null) => void
  loading: boolean; placeholder: string
}) {
  return (
    <label className="block text-sm">
      <span className="block font-medium text-neutral-700">{label}</span>
      <input
        type="number"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        disabled={loading}
        placeholder={placeholder}
        className="mt-1 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 disabled:opacity-50"
      />
    </label>
  )
}

function BoolField({
  label, value, onChange, loading,
}: {
  label: string; value: boolean
  onChange: (v: boolean) => void
  loading: boolean
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={!!value}
        disabled={loading}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4"
      />
      <span className="font-medium text-neutral-700">{label}</span>
    </label>
  )
}
