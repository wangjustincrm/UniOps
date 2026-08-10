import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface PortalUser {
  id: string
  email: string
  full_name: string
  role: string
  department_id: string | null
  /**
   * Server-side flag from /auth/me. True ⇒ the account must rotate its password
   * before using UniOps (admin-created first login, admin reset, or a password
   * past the Admin → Security expiry window). Portal blocks on it via
   * ProtectedRoute, and it rides the SSO handoff so sub-apps honour it too.
   */
  must_change_password?: boolean
}

interface AuthState {
  user: PortalUser | null
  token: string | null
  refreshToken: string | null
  mfaVerifiedAt: number | null
  mfaPendingToken: string | null
  isAuthenticated: boolean

  setUser: (user: PortalUser, token: string, refreshToken: string) => void
  clearMustChangePassword: () => void
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

      clearMustChangePassword: () =>
        set((state) =>
          state.user ? { user: { ...state.user, must_change_password: false } } : {}
        ),

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
