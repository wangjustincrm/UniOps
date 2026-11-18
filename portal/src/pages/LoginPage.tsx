import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff } from 'lucide-react'
import { Alert, AuthHeading, AuthLayout, Button, FormField, IconButton, Input } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { epmsApi, encodeSession, safeReturnUrl, goToReturnUrl } from '@/lib/api'
import { useBranding } from '@/hooks/useBranding'
import { MODULES } from '@/lib/modules'
import factoryPhoto from '@/assets/login-factory.jpg'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LoginResp  { access_token: string; refresh_token: string }
interface MfaResp    { mfa_required: true; mfa_token: string }
// must_change_password rides along from /auth/me — ProtectedRoute gates on it.
interface UserResp   { id: string; email: string; full_name: string; role: string; department_id: string | null; must_change_password?: boolean }

const EPMS_API_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'

async function fetchMe(token: string): Promise<UserResp> {
  const res = await fetch(`${EPMS_API_BASE}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Could not fetch user profile')
  return res.json()
}

/**
 * Where to go once the sign-in completes.
 *
 * A sub-app that found itself without a session sent the user here as
 * `?returnUrl=<its own href>` (EPMS/OA/VMS/Finance all do). Honour it by
 * handing the fresh session to that origin the same way the nav links do —
 * cross-origin localStorage is not shared, so without the `#__session=` hash
 * the module would just bounce the user straight back here. Anything that
 * fails safeReturnUrl()'s allow-list falls through to the Portal home.
 *
 * Returns true when it navigated away, so the caller skips its own navigate().
 */
function leaveForReturnUrl(user: UserResp, token: string, refreshToken: string): boolean {
  const target = safeReturnUrl(new URLSearchParams(window.location.search).get('returnUrl'))
  if (!target) return false
  goToReturnUrl(target, encodeSession(token, refreshToken, user))
  return true
}

// ── Login form ─────────────────────────────────────────────────────────────────

function LoginForm({ onMfaRequired }: { onMfaRequired: (token: string) => void }) {
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw]     = useState(false)
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)
  const { setUser } = useAuthStore()
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const resp = await epmsApi.post<LoginResp | MfaResp>('/auth/login', { email, password })
      if ('mfa_required' in resp) { onMfaRequired(resp.mfa_token); return }
      const user = await fetchMe(resp.access_token)
      setUser(user, resp.access_token, resp.refresh_token)
      if (leaveForReturnUrl(user, resp.access_token, resp.refresh_token)) return
      navigate('/')
    } catch (err: any) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <FormField label="Email" htmlFor="login-email">
        <Input
          id="login-email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@canadaroyalmilk.com"
          required
        />
      </FormField>
      <FormField label="Password" htmlFor="login-password">
        <div className="relative">
          <Input
            id="login-password"
            type={showPw ? 'text' : 'password'}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="pr-11"
          />
          <IconButton
            label={showPw ? 'Hide password' : 'Show password'}
            icon={showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            onClick={() => setShowPw((v) => !v)}
            type="button"
            className="absolute right-1 top-1"
          />
        </div>
      </FormField>

      {error && <Alert tone="danger">{error}</Alert>}

      <Button type="submit" loading={loading} className="mt-1 w-full">
        {loading ? 'Signing in…' : 'Sign in'}
      </Button>
    </form>
  )
}

// ── MFA form ──────────────────────────────────────────────────────────────────

function MfaForm({ mfaToken, onBack }: { mfaToken: string; onBack: () => void }) {
  const [code, setCode]       = useState('')
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  const { setUser, setMfaVerified } = useAuthStore()
  const navigate = useNavigate()
  const inputs = useRef<(HTMLInputElement | null)[]>([])

  const digits = code.split('')

  const handleDigit = (idx: number, val: string) => {
    const cleaned = val.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[idx] = cleaned
    setCode(next.join(''))
    if (cleaned && idx < 5) inputs.current[idx + 1]?.focus()
  }

  const handleKeyDown = (idx: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !digits[idx] && idx > 0) inputs.current[idx - 1]?.focus()
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (code.length < 6) { setError('Enter the 6-digit code'); return }
    setError('')
    setLoading(true)
    try {
      const resp = await epmsApi.post<{ access_token: string; refresh_token: string }>(
        '/auth/mfa/challenge', { mfa_token: mfaToken, code }
      )
      const user = await fetchMe(resp.access_token)
      setUser(user, resp.access_token, resp.refresh_token)
      setMfaVerified()
      if (leaveForReturnUrl(user, resp.access_token, resp.refresh_token)) return
      navigate('/')
    } catch (err: any) {
      setError(err.message || 'Invalid code')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-sm font-medium text-fg-secondary">Code</legend>
        <div className="grid grid-cols-6 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Input
              key={i}
              ref={(el) => { inputs.current[i] = el }}
              type="text"
              inputMode="numeric"
              autoComplete={i === 0 ? 'one-time-code' : 'off'}
              maxLength={1}
              value={digits[i] ?? ''}
              onChange={(e) => handleDigit(i, e.target.value)}
              onKeyDown={(e) => handleKeyDown(i, e)}
              aria-label={`Digit ${i + 1}`}
              className="h-12 px-0 text-center font-mono text-lg"
            />
          ))}
        </div>
      </fieldset>

      {error && <Alert tone="danger">{error}</Alert>}

      <Button type="submit" loading={loading} disabled={code.length < 6} className="mt-1 w-full">
        {loading ? 'Verifying…' : 'Verify'}
      </Button>
      <Button type="button" variant="link" onClick={onBack} className="self-center">
        Back to sign in
      </Button>
    </form>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// Module list on the brand panel comes from the module registry (same source as
// Portal Home), so modules added there appear here without touching this page.
const AUTH_MODULES = MODULES.map((m) => {
  const Icon = m.icon
  return { key: m.key, label: m.label, summary: m.summary, icon: <Icon /> }
})

export default function LoginPage() {
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const { data: branding } = useBranding()
  const name = branding?.name ?? 'UniOps'

  return (
    <AuthLayout
      brand={{ name, logoUrl: branding?.logo_data_url ?? null }}
      product="UniOps"
      headline="Every team’s daily work, on one platform"
      description="UniOps works alongside the ERP, so purchasing, expenses, sales, finance and operations run in one place."
      photoUrl={factoryPhoto}
      modules={AUTH_MODULES}
      footer={<>© {new Date().getFullYear()} {name}</>}
    >
      {mfaToken ? (
        <>
          <AuthHeading title="Verify it is you" description="Enter the 6-digit code from your authenticator app" />
          <MfaForm mfaToken={mfaToken} onBack={() => setMfaToken(null)} />
        </>
      ) : (
        <>
          <AuthHeading title="Sign in" description="Use your company email and password" />
          <LoginForm onMfaRequired={setMfaToken} />
        </>
      )}
    </AuthLayout>
  )
}
