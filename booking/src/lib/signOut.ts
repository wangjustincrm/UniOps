const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

export function signOut(): void {
  localStorage.removeItem('booking-auth')
  window.location.href = `${PORTAL_URL}/logout`
}
