import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Eye, EyeOff } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { epmsApi, encodeSession, safeReturnUrl, goToReturnUrl } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useBranding } from '@/hooks/useBranding'

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

// ── Shared field component ────────────────────────────────────────────────────

function Field({
  label, type, value, onChange, autoComplete, placeholder, required, suffix,
}: {
  label: string
  type: string
  value: string
  onChange: (v: string) => void
  autoComplete?: string
  placeholder?: string
  required?: boolean
  suffix?: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-semibold text-neutral-700">{label}</label>
      <div className="relative">
        <input
          type={type}
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          placeholder={placeholder}
          className="w-full rounded-xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-900 placeholder:text-neutral-400
            focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:bg-white
            transition-all duration-150"
        />
        {suffix && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">{suffix}</div>
        )}
      </div>
    </div>
  )
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
      <Field
        label="Email address"
        type="email"
        autoComplete="email"
        value={email}
        onChange={setEmail}
        placeholder="you@canadaroyalmilk.com"
        required
      />
      <Field
        label="Password"
        type={showPw ? 'text' : 'password'}
        autoComplete="current-password"
        value={password}
        onChange={setPassword}
        required
        suffix={
          <button
            type="button"
            onClick={() => setShowPw((v) => !v)}
            className="text-neutral-400 hover:text-neutral-600 transition-colors duration-150 cursor-pointer"
            aria-label={showPw ? 'Hide password' : 'Show password'}
          >
            {showPw
              ? <EyeOff className="h-4 w-4" />
              : <Eye className="h-4 w-4" />
            }
          </button>
        }
      />

      {error && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 border border-red-200 px-4 py-3">
          <svg className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          <p className="text-xs text-red-700">{error}</p>
        </div>
      )}

      <button
        type="submit"
        disabled={loading}
        className="flex items-center justify-center gap-2 rounded-xl bg-primary-700 px-4 py-3.5 text-sm font-semibold text-white
          hover:bg-primary-800 active:scale-[0.98] transition-all duration-150 disabled:opacity-60 cursor-pointer
          shadow-lg shadow-primary-700/20"
      >
        {loading && <Loader2 className="h-4 w-4 animate-spin" />}
        {loading ? 'Signing in…' : 'Sign In'}
      </button>
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
      <div className="rounded-xl bg-primary-50 border border-primary-100 px-4 py-3 text-center">
        <p className="text-sm text-primary-800 font-medium">Two-factor authentication</p>
        <p className="text-xs text-primary-600 mt-0.5">Enter the 6-digit code from your authenticator app</p>
      </div>

      <div className="flex justify-center gap-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <input
            key={i}
            ref={(el) => { inputs.current[i] = el }}
            type="text"
            inputMode="numeric"
            maxLength={1}
            value={digits[i] ?? ''}
            onChange={(e) => handleDigit(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            aria-label={`Digit ${i + 1}`}
            className={cn(
              'h-12 w-10 rounded-xl border text-center text-lg font-mono font-bold bg-neutral-50',
              'focus:outline-none focus:ring-2 focus:ring-primary-500/20 transition-all duration-150',
              digits[i]
                ? 'border-primary-400 bg-primary-50 text-primary-800'
                : 'border-neutral-200 text-neutral-900',
            )}
          />
        ))}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 border border-red-200 px-4 py-3">
          <svg className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          <p className="text-xs text-red-700">{error}</p>
        </div>
      )}

      <button
        type="submit"
        disabled={loading || code.length < 6}
        className="flex items-center justify-center gap-2 rounded-xl bg-primary-700 px-4 py-3.5 text-sm font-semibold text-white
          hover:bg-primary-800 active:scale-[0.98] transition-all duration-150 disabled:opacity-60 cursor-pointer
          shadow-lg shadow-primary-700/20"
      >
        {loading && <Loader2 className="h-4 w-4 animate-spin" />}
        {loading ? 'Verifying…' : 'Verify Code'}
      </button>

      <button
        type="button"
        onClick={onBack}
        className="flex items-center justify-center gap-1.5 text-sm text-neutral-400 hover:text-neutral-700 transition-colors duration-150 cursor-pointer"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
        Back to login
      </button>
    </form>
  )
}

// ── Left panel — branding ─────────────────────────────────────────────────────

const MODULES = [
  { icon: 'M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z', label: 'Procurement', sub: 'PR · PO · GR' },
  { icon: 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z', label: 'Finance', sub: 'Budget · AP' },
  { icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z', label: 'OA Platform', sub: 'Approvals · Expenses' },
  { icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', label: 'Sales CRM', sub: 'Orders · EDI' },
] as const

function BrandingPanel({ name, tagline, logoUrl, initials }: {
  name: string; tagline: string | null; logoUrl: string | null; initials: string
}) {
  return (
    <div className="relative hidden lg:flex flex-col justify-between overflow-hidden bg-primary-700 p-10 text-white">
      {/* Animated background blobs */}
      <div
        aria-hidden="true"
        className="absolute -top-24 -left-24 h-72 w-72 rounded-full bg-white/10 blur-3xl"
        style={{ animation: 'blobDrift 10s ease-in-out infinite' }}
      />
      <div
        aria-hidden="true"
        className="absolute -bottom-32 -right-20 h-80 w-80 rounded-full bg-white/8 blur-3xl"
        style={{ animation: 'blobDrift 14s ease-in-out 5s infinite reverse' }}
      />
      <div
        aria-hidden="true"
        className="absolute top-1/2 left-1/3 h-48 w-48 rounded-full bg-primary-500/30 blur-2xl"
        style={{ animation: 'blobDrift 12s ease-in-out 2s infinite' }}
      />

      {/* Logo + wordmark */}
      <div className="relative z-10 flex items-center gap-3">
        {logoUrl ? (
          <img src={logoUrl} alt={name} className="h-9 w-9 rounded-xl object-contain bg-white/20 p-1" />
        ) : (
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/20 text-sm font-bold">
            {initials}
          </div>
        )}
        <span className="text-lg font-bold tracking-tight" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
          {name}
        </span>
      </div>

      {/* Hero copy */}
      <div className="relative z-10 my-auto">
        <h2
          className="text-4xl font-extrabold leading-tight mb-4"
          style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
        >
          One platform.<br />Every operation.
        </h2>
        <p className="text-primary-200 text-base leading-relaxed max-w-xs">
          {tagline ?? 'Unified procurement, finance, OA, sales, and equipment management.'}
        </p>

        {/* Module cards */}
        <div className="mt-8 grid grid-cols-2 gap-3">
          {MODULES.map((m) => (
            <div
              key={m.label}
              className="flex items-center gap-3 rounded-2xl bg-white/10 backdrop-blur-sm px-4 py-3 border border-white/10"
            >
              <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-white/15">
                <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={m.icon} />
                </svg>
              </div>
              <div>
                <p className="text-xs font-semibold text-white leading-none">{m.label}</p>
                <p className="text-xs text-primary-300 mt-0.5">{m.sub}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Footer trust badges */}
      <div className="relative z-10 flex items-center gap-5 text-xs text-primary-300">
        <span className="flex items-center gap-1.5">
          <svg className="h-3.5 w-3.5 text-primary-400" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
          </svg>
          Canada data residency
        </span>
        <span className="flex items-center gap-1.5">
          <svg className="h-3.5 w-3.5 text-primary-400" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
          </svg>
          7-year audit trail
        </span>
        <span className="flex items-center gap-1.5">
          <svg className="h-3.5 w-3.5 text-primary-400" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
          </svg>
          MFA protected
        </span>
      </div>

      <style>{`
        @media (prefers-reduced-motion: no-preference) {
          @keyframes blobDrift {
            0%, 100% { transform: translate(0, 0) scale(1); }
            33%       { transform: translate(20px, -15px) scale(1.05); }
            66%       { transform: translate(-10px, 20px) scale(0.97); }
          }
        }
      `}</style>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function LoginPage() {
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const { data: branding } = useBranding()

  const name     = branding?.name ?? 'UniOps'
  const tagline  = branding?.tagline ?? null
  const logoUrl  = branding?.logo_data_url ?? null
  const initials = name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'U'

  return (
    <div className="min-h-screen grid lg:grid-cols-[5fr_4fr]">
      {/* Left: branding */}
      <BrandingPanel name={name} tagline={tagline} logoUrl={logoUrl} initials={initials} />

      {/* Right: form */}
      <div className="flex flex-col items-center justify-center bg-white px-6 py-12">
        {/* Mobile logo (hidden on desktop) */}
        <div className="mb-8 flex flex-col items-center lg:hidden">
          {logoUrl ? (
            <img src={logoUrl} alt={name} className="mb-3 h-12 w-12 rounded-xl object-contain" />
          ) : (
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary-700 text-white text-lg font-bold">
              {initials}
            </div>
          )}
          <span className="text-xl font-bold text-neutral-900" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
            {name}
          </span>
          {tagline && <p className="mt-1 text-xs text-neutral-500 text-center">{tagline}</p>}
        </div>

        {/* Form card */}
        <div className="w-full max-w-sm">
          {mfaToken ? (
            <>
              <div className="mb-8">
                <h1 className="text-2xl font-extrabold text-neutral-900" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
                  Verify identity
                </h1>
                <p className="mt-1 text-sm text-neutral-500">One more step to keep your account secure.</p>
              </div>
              <MfaForm mfaToken={mfaToken} onBack={() => setMfaToken(null)} />
            </>
          ) : (
            <>
              <div className="mb-8">
                <h1 className="text-2xl font-extrabold text-neutral-900" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>
                  Welcome back
                </h1>
                <p className="mt-1 text-sm text-neutral-500">Sign in to your {name} account.</p>
              </div>
              <LoginForm onMfaRequired={setMfaToken} />
            </>
          )}
        </div>

        {/* Footer */}
        <p className="mt-10 text-xs text-neutral-300 text-center">
          {name} · Internal Platform · © {new Date().getFullYear()}
        </p>
      </div>
    </div>
  )
}
