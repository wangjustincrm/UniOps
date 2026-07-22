import { useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/auth.store'
import { globalSignOut } from '@/lib/signOut'

const IDLE_MS = 60 * 60 * 1000 // 60 minutes
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
const EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'] as const

/**
 * Auto-logout after IDLE_MS of no user interaction.
 * Blacklists the refresh token on the server before clearing local state.
 * Must be mounted inside an authenticated layout.
 */
export function useIdleTimeout() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!isAuthenticated) return

    const handleIdle = async () => {
      const { refreshToken } = useAuthStore.getState()
      if (refreshToken) {
        try {
          await fetch(new URL(`${BASE}/auth/logout`, window.location.origin).toString(), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
          })
        } catch { /* ignore — still sign out locally */ }
      }
      globalSignOut()
    }

    const reset = () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(handleIdle, IDLE_MS)
    }

    EVENTS.forEach((e) => window.addEventListener(e, reset, { passive: true }))
    reset() // start the initial timer

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      EVENTS.forEach((e) => window.removeEventListener(e, reset))
    }
  }, [isAuthenticated])
}
