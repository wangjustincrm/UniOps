import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'

/**
 * Dedicated logout page — reachable from any UniOps module via redirect.
 * Clears Portal's own localStorage session and Zustand state, then sends
 * the user to the login page.
 *
 * Why a separate page? localStorage is origin-scoped. EPMS (:5173) and OA
 * (:5175) cannot remove Portal's (:5174) 'portal-auth' key directly.
 * They redirect here so Portal itself performs the cleanup.
 */
export default function LogoutPage() {
  const navigate = useNavigate()
  const { logout } = useAuthStore()

  useEffect(() => {
    logout()                                  // clears Zustand in-memory state
    localStorage.removeItem('portal-auth')    // remove persisted key immediately
    navigate('/login', { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}
