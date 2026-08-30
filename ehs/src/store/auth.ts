import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface MrpUser {
  id: string
  email: string
  full_name: string
  role: string
  department_id: string | null
}

interface AuthState {
  user: MrpUser | null
  token: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  setSession: (token: string, refreshToken: string, user: MrpUser) => void
  logout: () => void
}

export const useEhsAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      refreshToken: null,
      isAuthenticated: false,
      setSession: (token, refreshToken, user) =>
        set({ token, refreshToken, user, isAuthenticated: true }),
      logout: () =>
        set({ token: null, refreshToken: null, user: null, isAuthenticated: false }),
    }),
    { name: 'ehs-auth' }
  )
)
