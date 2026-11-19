/**
 * Themes — which ones exist, which one is active, and switching between them.
 *
 * A theme is a block of token values in packages/shell/themes/<id>.css
 * (`:root[data-theme="<id>"] { --color-…: … }`) plus one entry in THEMES below.
 * Nothing in the modules changes. See packages/shell/THEMING.md.
 */
import { useSyncExternalStore } from 'react'
// Plain-JS module shared with the Vite plugin (cookie name, default id).
import { DEFAULT_THEME, THEME_COOKIE } from '../../theme-boot.js'

export interface ThemeDef {
  /** data-theme value and file name: lowercase, digits, dashes. */
  id: string
  /** Shown in the user menu. */
  label: string
  /** light | dark — native controls and scrollbars follow it (color-scheme). */
  scheme: 'light' | 'dark'
}

/** Registered themes. The first is the default (what @theme in tokens.css defines). */
export const THEMES: readonly ThemeDef[] = [
  { id: DEFAULT_THEME, label: 'UniOps', scheme: 'light' },
  { id: 'crm', label: 'CRM', scheme: 'light' },
]

const listeners = new Set<() => void>()

function known(id: string | null | undefined): string {
  return id && THEMES.some((t) => t.id === id) ? id : DEFAULT_THEME
}

/** The active theme id (from <html data-theme>, set by the boot script). */
export function getTheme(): string {
  if (typeof document === 'undefined') return DEFAULT_THEME
  return known(document.documentElement.getAttribute('data-theme'))
}

/** Cookie domain: the parent domain in production (shared by every module), host-only elsewhere. */
function cookieDomain(host: string): string {
  const parts = host.split('.')
  if (host === 'localhost' || /^[\d.]+$/.test(host) || parts.length < 3) return ''
  return `; domain=.${parts.slice(-2).join('.')}`
}

/** Switch theme now and remember it for every module (1 year). */
export function setTheme(id: string): void {
  const next = known(id)
  const def = THEMES.find((t) => t.id === next)!
  const root = document.documentElement
  root.setAttribute('data-theme', next)
  root.style.colorScheme = def.scheme
  document.cookie = `${THEME_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax${cookieDomain(location.hostname)}`
  listeners.forEach((l) => l())
}

/** Current theme id; re-renders when it changes. */
export function useTheme(): [string, (id: string) => void] {
  const id = useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb) } },
    getTheme,
    () => DEFAULT_THEME,
  )
  return [id, setTheme]
}

export { DEFAULT_THEME, THEME_COOKIE }
