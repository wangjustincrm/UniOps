/**
 * Themes — which ones exist, which one is active, and switching between them.
 *
 * A theme is a block of token values in packages/shell/themes/<id>.css
 * (`:root[data-theme="<id>"] { --color-…: … }`) plus one entry in THEMES below.
 * Nothing in the modules changes. See packages/shell/THEMING.md.
 *
 * Which theme shows:
 *   1. the user's own choice (user menu), if any;
 *   2. otherwise the system default an admin picked (Portal → Admin → Company
 *      Settings), delivered by the public branding endpoint;
 *   3. otherwise "uniops".
 */
import { useSyncExternalStore } from 'react'
// Plain-JS module shared with the Vite plugin (cookie names, default id).
import { DEFAULT_THEME, THEME_COOKIE, THEME_DEFAULT_COOKIE } from '../../theme-boot.js'

export interface ThemeDef {
  /** data-theme value and file name: lowercase, digits, dashes. */
  id: string
  /** Shown in the user menu and the admin setting. */
  label: string
  /** light | dark — native controls and scrollbars follow it (color-scheme). */
  scheme: 'light' | 'dark'
}

/** Registered themes. The first is the built-in default (the @theme block in tokens.css). */
export const THEMES: readonly ThemeDef[] = [
  { id: DEFAULT_THEME, label: 'UniOps', scheme: 'light' },
  { id: 'crm', label: 'CRM', scheme: 'light' },
]

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

function known(id: string | null | undefined): string | null {
  return id && THEMES.some((t) => t.id === id) ? id : null
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([a-z0-9-]+)`))
  return m ? m[1] : null
}

/** Cookie domain: the parent domain in production (shared by every module), host-only elsewhere. */
function cookieDomain(host: string): string {
  const parts = host.split('.')
  if (host === 'localhost' || /^[\d.]+$/.test(host) || parts.length < 3) return ''
  return `; domain=.${parts.slice(-2).join('.')}`
}

function writeCookie(name: string, value: string | null): void {
  const base = `; path=/; samesite=lax${cookieDomain(location.hostname)}`
  document.cookie = value === null ? `${name}=${base}; max-age=0` : `${name}=${value}${base}; max-age=31536000`
}

/** The user's own choice, or null when they follow the system default. */
export function getThemeChoice(): string | null {
  return known(readCookie(THEME_COOKIE))
}

/** The system default an admin set (as last seen from the branding endpoint). */
export function getSystemTheme(): string {
  return known(readCookie(THEME_DEFAULT_COOKIE)) ?? DEFAULT_THEME
}

/** The theme on screen now. */
export function getTheme(): string {
  return getThemeChoice() ?? getSystemTheme()
}

function apply(): void {
  if (typeof document === 'undefined') return
  const id = getTheme()
  const root = document.documentElement
  root.setAttribute('data-theme', id)
  root.style.colorScheme = THEMES.find((t) => t.id === id)!.scheme
  notify()
}

/** The user picks a theme for themselves (every module, 1 year); null = follow the system default. */
export function setTheme(id: string | null): void {
  writeCookie(THEME_COOKIE, id === null ? null : known(id) ?? DEFAULT_THEME)
  apply()
}

/**
 * Adopt the system default (from the public branding endpoint, or right after
 * an admin saves it). Users with their own choice keep it. Unknown ids — a
 * theme the server names that this build does not ship — fall back to "uniops".
 */
export function setSystemTheme(id: string | null | undefined): void {
  if (typeof document === 'undefined' || !id) return
  const next = known(id) ?? DEFAULT_THEME
  if (readCookie(THEME_DEFAULT_COOKIE) !== next) writeCookie(THEME_DEFAULT_COOKIE, next)
  apply()
}

export interface ThemeState {
  /** Theme on screen. */
  theme: string
  /** The user's own choice, or null = following the system default. */
  choice: string | null
  /** The system default. */
  system: string
  setTheme: (id: string | null) => void
}

let snapshot: ThemeState | null = null
function getState(): ThemeState {
  const theme = getTheme(), choice = getThemeChoice(), system = getSystemTheme()
  if (!snapshot || snapshot.theme !== theme || snapshot.choice !== choice || snapshot.system !== system) {
    snapshot = { theme, choice, system, setTheme }
  }
  return snapshot
}
const serverState: ThemeState = { theme: DEFAULT_THEME, choice: null, system: DEFAULT_THEME, setTheme }

/** Theme state; re-renders when the theme, the choice or the system default changes. */
export function useTheme(): ThemeState {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb) } },
    getState,
    () => serverState,
  )
}

export { DEFAULT_THEME, THEME_COOKIE, THEME_DEFAULT_COOKIE }
