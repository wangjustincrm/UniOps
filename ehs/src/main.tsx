import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useEhsAuth } from './store/auth'

// ── Portal session handoff ────────────────────────────────────────────────────
// Portal redirects to `${SAFETY_URL}/#__session=<base64>` after login. We pluck
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

    const ehsUser = {
      id: user.id,
      email: user.email,
      full_name: user.full_name ?? user.name ?? '',
      role: user.role,
      department_id: user.department_id ?? null,
    }

    // 1. Update in-memory Zustand store
    useEhsAuth.setState({
      token,
      refreshToken: refreshToken ?? null,
      user: ehsUser,
      isAuthenticated: true,
    })

    // 2. Persist to localStorage
    const existing = JSON.parse(localStorage.getItem('ehs-auth') ?? '{}')
    localStorage.setItem('ehs-auth', JSON.stringify({
      ...existing,
      state: {
        ...(existing.state ?? {}),
        token,
        refreshToken: refreshToken ?? null,
        user: ehsUser,
        isAuthenticated: true,
      },
      version: 0,
    }))
  } catch { /* ignore malformed session */ }
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

// ── Installable as an app (round 10, q10-app) ─────────────────────────────────
// Only where the browser allows a service worker at all: https, or localhost.
// The test server is plain http, so there it stays a website — production is
// where "Add to Home Screen" gives a full-screen app. public/sw.js says what
// the worker does (offline notice only, nothing cached) and how to withdraw it.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* a website still */ })
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
