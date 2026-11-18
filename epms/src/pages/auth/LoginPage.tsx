import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Eye, EyeOff } from 'lucide-react'
import { Alert, AuthHeading, AuthLayout, IconButton } from '@uniops/shell'
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
    <AuthLayout
      brand={{ name: branding?.name ?? 'EPMS', logoUrl: branding?.logo_data_url ?? null }}
      product="UniOps"
      headline="Every team’s daily work, on one platform"
      description="UniOps works alongside the ERP, so purchasing, expenses, sales, finance and operations run in one place."
      footer={<>© {new Date().getFullYear()} {branding?.name ?? 'UniOps'}</>}
    >
      <AuthHeading title="Sign in" description={branding?.tagline || 'Use your company email and password'} />
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
              autoComplete="current-password"
              error={!!errors.password}
              className="pr-11"
              {...register('password')}
            />
            <IconButton
              type="button"
              label={showPassword ? 'Hide password' : 'Show password'}
              icon={showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              onClick={() => setShowPassword((v) => !v)}
              className="absolute right-1 top-1"
            />
          </div>
        </FormField>

        {serverError && <Alert tone="danger">{serverError}</Alert>}

        <Button type="submit" className="mt-1 w-full" loading={isSubmitting}>
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </AuthLayout>
  )
}
