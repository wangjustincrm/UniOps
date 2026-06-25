import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Eye, EyeOff, Building2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { useAuthStore } from '@/stores/auth.store'
import { authService } from '@/services/auth'
import type { User } from '@/types'

interface Branding { name: string; tagline: string; logo_data_url: string | null }

async function fetchBranding(): Promise<Branding | null> {
  try {
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const res = await fetch(`${base}/config/public/branding?module=epms`)
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

const loginSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})
type LoginForm = z.infer<typeof loginSchema>

export default function LoginPage() {
  const navigate = useNavigate()
  const { setUser, setMfaVerified, setMfaPending } = useAuthStore()
  const [showPassword, setShowPassword] = useState(false)
  const [serverError, setServerError] = useState('')
  const [branding, setBranding] = useState<Branding | null>(null)

  useEffect(() => { fetchBranding().then(setBranding) }, [])

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) })

  const onSubmit = async (data: LoginForm) => {
    setServerError('')
    try {
      const res = await authService.login(data.email, data.password)

      if ('mfa_required' in res && res.mfa_required) {
        // MFA step required — store mfa_token and redirect
        setMfaPending(res.mfa_token)
        navigate('/mfa')
        return
      }

      // Full login — fetch user profile
      const tokens = res as { access_token: string; refresh_token: string }
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
    } catch (err) {
      setServerError(err instanceof Error ? err.message : 'Invalid email or password.')
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-600 to-primary-800 flex items-center justify-center p-4">
      <div className="w-full max-w-[480px]">
        {/* Card */}
        <div className="rounded-xl bg-white shadow-xl px-8 py-10">
          {/* Logo */}
          <div className="mb-8 flex flex-col items-center gap-3">
            {branding?.logo_data_url ? (
              <img src={branding.logo_data_url} alt="Company logo" className="h-14 w-14 rounded-xl object-contain" />
            ) : (
              <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-primary-600 text-white">
                <Building2 className="h-8 w-8" />
              </div>
            )}
            <div className="text-center">
              <h1 className="text-2xl font-bold text-neutral-900">{branding?.name ?? 'EPMS'}</h1>
              <p className="text-sm text-neutral-500">{branding?.tagline ?? 'Enterprise Procurement Management System'}</p>
            </div>
          </div>

          <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-5">
            <FormField label="Email" required htmlFor="email" error={errors.email?.message}>
              <Input
                id="email"
                type="email"
                placeholder="you@company.ca"
                autoComplete="email"
                error={!!errors.email}
                {...register('email')}
              />
            </FormField>

            <FormField label="Password" required htmlFor="password" error={errors.password?.message}>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  error={!!errors.password}
                  className="pr-10"
                  {...register('password')}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </FormField>

            {serverError && (
              <div
                className="rounded-md bg-danger-50 border border-danger-200 px-4 py-3 text-sm text-danger-600"
                role="alert"
              >
                {serverError}
              </div>
            )}

            <Button type="submit" size="lg" className="w-full mt-1" disabled={isSubmitting}>
              {isSubmitting ? 'Signing in…' : 'Sign In'}
            </Button>
          </form>

          <div className="mt-5 text-center">
            <a href="#" className="text-sm text-primary-600 hover:underline">
              Forgot password?
            </a>
          </div>
        </div>
      </div>
    </div>
  )
}
