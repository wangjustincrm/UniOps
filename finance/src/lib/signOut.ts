const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

/**
 * Finance global sign-out.
 *
 * localStorage is origin-scoped: Finance (:5177) can only clear its own keys.
 * We clear the Finance session here, then redirect to Portal's /logout route so
 * Portal (:5174) clears its own session and sends the user to the login page —
 * a true system-wide sign-out (mirrors EPMS lib/signOut.ts). A bare redirect to
 * Portal would land on the still-authenticated Portal home instead.
 */
export function globalSignOut(): void {
  localStorage.removeItem('portal-auth') // Finance's persisted auth store (origin :5177)
  window.location.href = `${PORTAL_URL}/logout`
}
