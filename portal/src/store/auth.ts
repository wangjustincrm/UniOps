import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface PortalUser {
  id: string
  email: string
  full_name: string
  role: string
  department_id: string | null
}

interface AuthState {
  user: PortalUser | null
  token: string | null
  refreshToken: string | null
  mfaVerifiedAt: number | null
  mfaPendingToken: string | null
  isAuthenticated: boolean

  setUser: (user: PortalUser, token: string, refreshToken: string) => void
  setMfaPending: (mfaToken: string) => void
  setMfaVerified: () => void
  isMfaValid: () => boolean
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      refreshToken: null,
      mfaVerifiedAt: null,
      mfaPendingToken: null,
      isAuthenticated: false,

      setUser: (user, token, refreshToken) =>
        set({ user, token, refreshToken, isAuthenticated: true, mfaPendingToken: null }),

      setMfaPending: (mfaToken) =>
        set({ mfaPendingToken: mfaToken }),

      setMfaVerified: () =>
        set({ mfaVerifiedAt: Date.now() }),

      isMfaValid: () => {
        const { mfaVerifiedAt } = get()
        if (!mfaVerifiedAt) return false
        return Date.now() - mfaVerifiedAt < 8 * 60 * 60 * 1000
      },

      logout: () =>
        set({
          user: null, token: null, refreshToken: null,
          mfaVerifiedAt: null, mfaPendingToken: null, isAuthenticated: false,
        }),
    }),
    { name: 'portal-auth' }
  )
)
