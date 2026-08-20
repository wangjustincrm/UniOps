import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useAuthStore } from './stores/auth.store'

// ── Portal session handoff ────────────────────────────────────────────────────
// Zustand persist reads localStorage during import (above), so we must call
// setState() to update the in-memory store — writing to localStorage alone
// is too late (the store is already initialized with the old/empty values).
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

    // Portal user has full_name; EPMS User type expects name
    const epmsUser = {
      id: user.id,
      email: user.email,
      name: user.full_name ?? user.name ?? '',
      role: user.role,
      department_id: user.department_id ?? null,
    }
    const mfaVerifiedAt = Date.now()
    // Honour the Portal's rotation flag instead of assuming a clean session —
    // an expired or never-rotated password must still hit the forced modal
    // after the handoff. Older Portal builds omit the field ⇒ falsy ⇒ no gate.
    const mustChangePassword = user.must_change_password === true

    // 1. Update in-memory Zustand store (React will mount with this state)
    useAuthStore.setState({
      token,
      refreshToken: refreshToken ?? null,
      user: epmsUser,
      isAuthenticated: true,
      mfaVerifiedAt,
      mfaPendingToken: null,
      mustChangePassword,
    })

    // 2. Persist to localStorage so refreshes also work
    const existing = JSON.parse(localStorage.getItem('epms-auth') ?? '{}')
    localStorage.setItem('epms-auth', JSON.stringify({
      ...existing,
      state: {
        ...(existing.state ?? {}),
        token,
        refreshToken: refreshToken ?? null,
        user: epmsUser,
        isAuthenticated: true,
        mfaVerifiedAt,
        mfaPendingToken: null,
        mustChangePassword,
      },
      version: 0,
    }))
  } catch { /* ignore malformed session */ }

  // Strip the hash so it doesn't appear in the address bar
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
