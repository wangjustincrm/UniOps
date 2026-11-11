import './lib/randomUUIDPolyfill'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useBookingAuth } from './store/auth'
import { loadDisplayTimeZone } from '@uniops/shell'

// ── Portal session handoff ──────────────────────────────────────────────────
// Portal redirects to `${BOOKING_URL}/#__session=<base64>` after login. We pluck
// the session out of the hash, hydrate Zustand + localStorage, then strip the
// hash so it never lingers in the address bar.
// Mirror of the portal/finance encoder: base64 → UTF-8 bytes → string. atob()
// alone mangles non-Latin1 characters (e.g. Chinese full_name).
function decodeUtf8Base64(b64: string): string {
  const bin = atob(b64)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}
;(function bootstrapFromPortal() {
  const hash = window.location.hash
  const match = hash.match(/__session=([^&]+)/)
  if (!match) return
  try {
    const { token, refreshToken, user } = JSON.parse(decodeUtf8Base64(match[1]))
    if (!token || !user) return

    const bookingUser = {
      id: user.id,
      email: user.email,
      full_name: user.full_name ?? user.name ?? '',
      role: user.role,
      department_id: user.department_id ?? null,
    }

    // 1. In-memory store
    useBookingAuth.setState({
      token,
      refreshToken: refreshToken ?? null,
      user: bookingUser,
      isAuthenticated: true,
    })

    // 2. localStorage (so it survives reload)
    const existing = JSON.parse(localStorage.getItem('booking-auth') ?? '{}')
    localStorage.setItem('booking-auth', JSON.stringify({
      ...existing,
      state: {
        ...(existing.state ?? {}),
        token,
        refreshToken: refreshToken ?? null,
        user: bookingUser,
        isAuthenticated: true,
      },
      version: 0,
    }))
  } catch { /* malformed handoff — ignore */ }
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

// The plant's time zone (Portal → Admin → Company Settings) before the first
// render, so every time on screen is plant time (UI-DATE-002). Never blocks
// for long: on failure the zone last seen on this device is used.
void loadDisplayTimeZone(`${(import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'}/api/v1/config/public/branding`).finally(() =>
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
))
