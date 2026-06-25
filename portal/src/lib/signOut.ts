/**
 * Portal-side global sign-out.
 *
 * Portal is already on the correct origin so it can clear its own localStorage
 * key directly. EPMS/OA sessions on other origins are cleared by redirecting
 * those apps to this same origin's /logout route (see EPMS lib/signOut.ts).
 */
export function globalSignOut(): void {
  localStorage.removeItem('portal-auth')
  window.location.href = '/login'
}
