import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useVmsAuth } from './store/auth'

// ── Portal session handoff ──────────────────────────────────────────────────
// Portal redirects to `${VMS_URL}/#__session=<base64>` after login. We pluck
// the session out of the hash, hydrate Zustand + localStorage, then strip the
// hash so it never lingers in the address bar.
;(function bootstrapFromPortal() {
  const hash = window.location.hash
  const match = hash.match(/__session=([^&]+)/)
  if (!match) return
  try {
    const { token, refreshToken, user } = JSON.parse(atob(match[1]))
    if (!token || !user) return

    const vmsUser = {
      id: user.id,
      email: user.email,
      full_name: user.full_name ?? user.name ?? '',
      role: user.role,
      department_id: user.department_id ?? null,
    }

    // 1. In-memory store
    useVmsAuth.setState({
      token,
      refreshToken: refreshToken ?? null,
      user: vmsUser,
      isAuthenticated: true,
    })

    // 2. localStorage (so it survives reload)
    const existing = JSON.parse(localStorage.getItem('vms-auth') ?? '{}')
    localStorage.setItem('vms-auth', JSON.stringify({
      ...existing,
      state: {
        ...(existing.state ?? {}),
        token,
        refreshToken: refreshToken ?? null,
        user: vmsUser,
        isAuthenticated: true,
      },
      version: 0,
    }))
  } catch { /* malformed handoff — ignore */ }
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
