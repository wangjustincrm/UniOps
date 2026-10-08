import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth.store'
import { authService } from '@/services/auth'
import type { User } from '@/types'

interface Branding { name: string; tagline: string; logo_data_url: string | null }

async function fetchBranding(): Promise<Branding | null> {
  try {
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const res = await fetch(`${base}/config/public/branding`)
    if (!res.ok) return null
    return res.json()
  } catch { return null }
}

export default function MfaPage() {
  const navigate = useNavigate()
  const { mfaPendingToken, isAuthenticated, setUser, setMfaVerified } = useAuthStore()
  const [digits, setDigits] = useState(['', '', '', '', '', ''])
  const [error, setError] = useState('')
  const [isVerifying, setIsVerifying] = useState(false)
  const [resendStatus, setResendStatus] = useState<'idle' | 'sending' | 'sent' | 'wait'>('idle')
  const inputs = useRef<(HTMLInputElement | null)[]>([])
  const [branding, setBranding] = useState<Branding | null>(null)

  useEffect(() => { fetchBranding().then(setBranding) }, [])

  // Without a challenge token this page cannot do anything — /auth/mfa/challenge
  // has nothing to verify against. It used to stay put whenever the user merely
  // looked authenticated, which left anyone arriving here with a live session
  // but no pending challenge staring at a code box that could never succeed.
  useEffect(() => {
    if (mfaPendingToken) return
    navigate(isAuthenticated ? '/dashboard' : '/login', { replace: true })
  }, [mfaPendingToken, isAuthenticated, navigate])

  if (!mfaPendingToken) return null

  const handleChange = (index: number, value: string) => {
    if (!/^\d?$/.test(value)) return
    const next = [...digits]
    next[index] = value
    setDigits(next)
    setError('')
    if (value && index < 5) inputs.current[index + 1]?.focus()
    if (value && index === 5) {
      const code = [...next.slice(0, 5), value].join('')
      if (code.length === 6) handleVerify([...next.slice(0, 5), value])
    }
  }

  const handleKeyDown = (index: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      inputs.current[index - 1]?.focus()
    }
  }

  const handlePaste = (e: React.ClipboardEvent) => {
    e.preventDefault()
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    if (pasted.length === 6) {
      setDigits(pasted.split(''))
      handleVerify(pasted.split(''))
    }
  }

  const handleResend = async () => {
    if (!mfaPendingToken) return
    setResendStatus('sending')
    try {
      await authService.mfaResend(mfaPendingToken)
      setResendStatus('sent')
      setTimeout(() => setResendStatus('idle'), 30_000)
    } catch (e) {
      setResendStatus('wait')
      setTimeout(() => setResendStatus('idle'), 60_000)
    }
  }

  const handleVerify = async (digitArr = digits) => {
    const code = digitArr.join('')
    if (code.length < 6) { setError('Please enter all 6 digits.'); return }
    setIsVerifying(true)
    try {
      const tokens = await authService.mfaChallenge(mfaPendingToken, code)
      const apiUser = await authService.me(tokens.access_token)
      const user: User = {
        id: apiUser.id,
        name: apiUser.full_name,
        email: apiUser.email,
        role: apiUser.role as User['role'],
        isActive: apiUser.is_active,
        department_id: apiUser.department_id ? String(apiUser.department_id) : null,
      }
      setUser(user, tokens.access_token, tokens.refresh_token, apiUser.must_change_password)
      setMfaVerified()
      navigate('/dashboard')
    } catch {
      setError('Invalid code. Please try again.')
      setDigits(['', '', '', '', '', ''])
      inputs.current[0]?.focus()
    } finally {
      setIsVerifying(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-600 to-primary-800 flex items-center justify-center p-4">
      <div className="w-full max-w-[420px]">
        <div className="rounded-xl bg-white shadow-xl px-8 py-10">
          <div className="mb-8 flex flex-col items-center gap-3">
            {branding?.logo_data_url ? (
              <img src={branding.logo_data_url} alt="Company logo" className="h-12 w-12 rounded-xl object-contain" />
            ) : (
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-600 text-white">
                <Building2 className="h-7 w-7" />
              </div>
            )}
            <div className="text-center">
              <h1 className="text-xl font-bold text-neutral-900">Email Verification</h1>
              <p className="text-sm text-neutral-500 mt-1">A 6-digit code has been sent to your registered email address</p>
            </div>
          </div>

          <div className="flex flex-col items-center gap-6">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary-50">
              <Mail className="h-7 w-7 text-primary-600" />
            </div>

            <div className="flex gap-2" onPaste={handlePaste} role="group" aria-label="One-time password">
              {digits.map((d, i) => (
                <input
                  key={i}
                  ref={(el) => { inputs.current[i] = el }}
                  type="text"
                  inputMode="numeric"
                  maxLength={1}
                  value={d}
                  onChange={(e) => handleChange(i, e.target.value)}
                  onKeyDown={(e) => handleKeyDown(i, e)}
                  className={[
                    'h-12 w-11 rounded-md border text-center text-xl font-semibold',
                    'focus:outline-none focus:ring-2 focus:ring-primary-600',
                    error ? 'border-danger-600' : 'border-neutral-300',
                  ].join(' ')}
                  aria-label={`Digit ${i + 1}`}
                />
              ))}
            </div>

            {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}

            <Button
              onClick={() => handleVerify()}
              size="lg"
              className="w-full"
              disabled={isVerifying || digits.some((d) => !d)}
            >
              {isVerifying ? 'Verifying…' : 'Verify'}
            </Button>

            <div className="flex gap-4 text-sm">
              <button
                type="button"
                onClick={handleResend}
                disabled={resendStatus === 'sending' || resendStatus === 'sent' || resendStatus === 'wait'}
                className="text-primary-600 hover:underline disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {resendStatus === 'sending' ? 'Sending…'
                  : resendStatus === 'sent' ? 'Code sent!'
                  : resendStatus === 'wait' ? 'Please wait…'
                  : 'Resend code'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
