import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useOaAuth } from './store/auth'

// ── Portal session handoff ────────────────────────────────────────────────────
;(function bootstrapFromPortal() {
  const hash = window.location.hash
  const match = hash.match(/__session=([^&]+)/)
  if (!match) return
  try {
    const { token, refreshToken, user } = JSON.parse(atob(match[1]))
    if (!token || !user) return

    const oaUser = {
      id: user.id,
      email: user.email,
      full_name: user.full_name ?? user.name ?? '',
      role: user.role,
      department_id: user.department_id ?? null,
    }

    // 1. Update in-memory Zustand store
    useOaAuth.setState({
      token,
      refreshToken: refreshToken ?? null,
      user: oaUser,
      isAuthenticated: true,
    })

    // 2. Persist to localStorage
    const existing = JSON.parse(localStorage.getItem('oa-auth') ?? '{}')
    localStorage.setItem('oa-auth', JSON.stringify({
      ...existing,
      state: {
        ...(existing.state ?? {}),
        token,
        refreshToken: refreshToken ?? null,
        user: oaUser,
        isAuthenticated: true,
      },
      version: 0,
    }))
  } catch { /* ignore malformed session */ }
  history.replaceState(null, '', window.location.pathname + window.location.search)
})()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
