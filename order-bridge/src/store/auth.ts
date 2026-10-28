import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface ModuleUser {
  id: string
  email: string
  full_name: string
  role: string
  department_id: string | null
}

interface AuthState {
  user: ModuleUser | null
  token: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  setSession: (token: string, refreshToken: string, user: ModuleUser) => void
  logout: () => void
}

export const useModuleAuth = create<AuthState>()(
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
    { name: 'order-bridge-auth' }
  )
)
