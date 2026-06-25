import { api } from '@/lib/api'

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface MfaRequiredResponse {
  mfa_required: true
  mfa_token: string
}

export type NotificationChannel = 'email_only' | 'teams_only' | 'both' | 'none'

export interface ApiUser {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
  department_id: string | null
  teams_account?: string | null
  notification_channel?: NotificationChannel
  mfa_enabled?: boolean
  must_change_password?: boolean
}

export const authService = {
  login: (email: string, password: string) =>
    api.post<LoginResponse | MfaRequiredResponse>('/auth/login', { email, password }),

  mfaChallenge: (mfa_token: string, code: string) =>
    api.post<LoginResponse>('/auth/mfa/challenge', { mfa_token, code }),

  refresh: (refresh_token: string) =>
    api.post<LoginResponse>('/auth/refresh', { refresh_token }),

  logout: (refresh_token: string) =>
    api.post<void>('/auth/logout', { refresh_token }),

  mfaEnable: () =>
    api.post<void>('/auth/mfa/enable'),

  mfaDisable: () =>
    api.post<void>('/auth/mfa/disable'),

  mfaResend: (mfa_token: string) =>
    api.post<void>('/auth/mfa/resend', { mfa_token }),

  /** Fetch the current user. Pass accessToken explicitly right after login
   *  (before the token is persisted to localStorage). */
  me: async (accessToken?: string): Promise<ApiUser> => {
    if (!accessToken) return api.get<ApiUser>('/auth/me')
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const res = await fetch(`${base}/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!res.ok) throw new Error('Could not fetch user profile')
    return res.json() as Promise<ApiUser>
  },
}
