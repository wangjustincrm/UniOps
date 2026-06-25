import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  token: string | null           // access token
  refreshToken: string | null    // refresh token (for silent refresh)
  mfaVerifiedAt: number | null
  mfaPendingToken: string | null // short-lived mfa_token during 2-step login
  isAuthenticated: boolean
  mustChangePassword: boolean

  setUser: (user: User, token: string, refreshToken: string, mustChangePassword?: boolean) => void
  updateUser: (partial: Partial<User>) => void
  updateTokens: (token: string, refreshToken: string) => void
  setMfaVerified: () => void
  setMfaPending: (mfaToken: string) => void
  isMfaValid: () => boolean
  clearMustChangePassword: () => void
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
      mustChangePassword: false,

      setUser: (user, token, refreshToken, mustChangePassword = false) =>
        set({ user, token, refreshToken, isAuthenticated: true, mfaPendingToken: null, mustChangePassword }),

      updateUser: (partial) =>
        set((state) => ({ user: state.user ? { ...state.user, ...partial } : state.user })),

      updateTokens: (token, refreshToken) =>
        set({ token, refreshToken }),

      setMfaVerified: () =>
        set({ mfaVerifiedAt: Date.now() }),

      setMfaPending: (mfaToken: string) =>
        set({ mfaPendingToken: mfaToken }),

      isMfaValid: () => {
        const { mfaVerifiedAt } = get()
        if (!mfaVerifiedAt) return false
        // MFA valid for 8 hours
        return Date.now() - mfaVerifiedAt < 8 * 60 * 60 * 1000
      },

      clearMustChangePassword: () =>
        set({ mustChangePassword: false }),

      logout: () =>
        set({
          user: null,
          token: null,
          refreshToken: null,
          mfaVerifiedAt: null,
          mfaPendingToken: null,
          isAuthenticated: false,
          mustChangePassword: false,
        }),
    }),
    { name: 'epms-auth' }
  )
)
