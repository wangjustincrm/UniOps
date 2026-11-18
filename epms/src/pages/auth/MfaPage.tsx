import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, AuthHeading, AuthLayout, Input } from '@uniops/shell'
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
    <AuthLayout
      brand={{ name: branding?.name ?? 'EPMS', logoUrl: branding?.logo_data_url ?? null }}
      product="UniOps"
      headline="Every team’s daily work, on one platform"
      description="UniOps works alongside the ERP, so purchasing, expenses, sales, finance and operations run in one place."
      footer={<>© {new Date().getFullYear()} {branding?.name ?? 'UniOps'}</>}
    >
      <AuthHeading title="Verify it is you" description="A 6-digit code has been sent to your registered email address" />
      <div className="flex flex-col gap-5">
        <div className="grid grid-cols-6 gap-2" onPaste={handlePaste} role="group" aria-label="One-time password">
          {digits.map((d, i) => (
            <Input
              key={i}
              ref={(el) => { inputs.current[i] = el }}
              type="text"
              inputMode="numeric"
              autoComplete={i === 0 ? 'one-time-code' : 'off'}
              maxLength={1}
              value={d}
              error={!!error}
              onChange={(e) => handleChange(i, e.target.value)}
              onKeyDown={(e) => handleKeyDown(i, e)}
              className="h-12 px-0 text-center font-mono text-lg"
              aria-label={`Digit ${i + 1}`}
            />
          ))}
        </div>

        {error && <Alert tone="danger">{error}</Alert>}

        <Button onClick={() => handleVerify()} className="w-full" loading={isVerifying} disabled={digits.some((d) => !d)}>
          {isVerifying ? 'Verifying…' : 'Verify'}
        </Button>

        <Button
          variant="link"
          className="self-center"
          onClick={handleResend}
          disabled={resendStatus === 'sending' || resendStatus === 'sent' || resendStatus === 'wait'}
        >
          {resendStatus === 'sending' ? 'Sending…'
            : resendStatus === 'sent' ? 'Code sent'
            : resendStatus === 'wait' ? 'Please wait…'
            : 'Resend code'}
        </Button>
      </div>
    </AuthLayout>
  )
}
