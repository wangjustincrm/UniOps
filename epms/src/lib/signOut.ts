const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

/**
 * EPMS global sign-out.
 *
 * localStorage is origin-scoped: EPMS (:5173) can only clear its own keys.
 * We clear 'epms-auth' here, then redirect to Portal's /logout route so Portal
 * (:5174) can clear 'portal-auth' from its own origin and send the user to
 * the login page.
 *
 * OA's 'oa-auth' will persist until OA is next visited, where a missing or
 * expired token causes a redirect to the Portal login page automatically.
 */
export function globalSignOut(): void {
  localStorage.removeItem('epms-auth')
  window.location.href = `${PORTAL_URL}/logout`
}
