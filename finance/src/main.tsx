import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useAuthStore } from './store/auth'

// ── Portal session handoff ──────────────────────────────────────────────────
// Portal redirects to `${FINANCE_URL}/#__session=<base64json>` after login. We
// pluck the session out of the hash, hydrate the auth store, then strip the hash.
// Mirror of the portal/finance encoder: base64 → UTF-8 bytes → string. atob()
// alone mangles non-Latin1 characters (e.g. Chinese full_name).
function decodeUtf8Base64(b64: string): string {
  const bin = atob(b64)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}
;(function bootstrapFromPortal() {
  const match = window.location.hash.match(/__session=([^&]+)/)
  if (!match) return
  try {
    const { token, refreshToken, user } = JSON.parse(decodeUtf8Base64(match[1]))
    if (!token || !user) return
    useAuthStore.getState().setUser(
      {
        id: user.id,
        email: user.email,
        full_name: user.full_name ?? user.name ?? '',
        role: user.role,
        department_id: user.department_id ?? null,
      },
      token,
      refreshToken ?? '',
    )
    useAuthStore.getState().setMfaVerified()
  } catch {
    /* malformed handoff — ignore */
  }
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
