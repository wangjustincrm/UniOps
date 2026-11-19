import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { THEME_BOOT_SCRIPT } from '../../theme-boot.js'
import { DEFAULT_THEME, THEMES, getTheme, getThemeChoice, getSystemTheme, setSystemTheme, setTheme, useTheme } from './theme'
import { UserMenu } from '../ui/chrome'
import { loadDisplayTimeZone } from '../lib/date'

type ThemeDefList = { id: string; label: string; scheme: 'light' | 'dark' }[]

function reset() {
  document.cookie = 'uniops-theme=; path=/; max-age=0'
  document.cookie = 'uniops-theme-default=; path=/; max-age=0'
  document.documentElement.removeAttribute('data-theme')
}
afterEach(reset)

describe('theme boot script', () => {
  it('own choice, else the system default, else uniops', () => {
    new Function(THEME_BOOT_SCRIPT)()
    expect(document.documentElement.getAttribute('data-theme')).toBe(DEFAULT_THEME)
    document.cookie = 'uniops-theme-default=crm; path=/'
    new Function(THEME_BOOT_SCRIPT)()
    expect(document.documentElement.getAttribute('data-theme')).toBe('crm')
    document.cookie = 'uniops-theme=uniops; path=/'
    new Function(THEME_BOOT_SCRIPT)()
    expect(document.documentElement.getAttribute('data-theme')).toBe('uniops')
  })
})

describe('system default vs own choice', () => {
  it('the system default applies to users without a choice', () => {
    setSystemTheme('crm')
    expect(getSystemTheme()).toBe('crm')
    expect(getThemeChoice()).toBeNull()
    expect(getTheme()).toBe('crm')
    expect(document.documentElement.getAttribute('data-theme')).toBe('crm')
  })

  it('an own choice wins over the system default; null goes back to following it', () => {
    setSystemTheme('crm')
    setTheme('uniops')
    expect(getTheme()).toBe('uniops')
    setSystemTheme('crm')                       // e.g. the next branding fetch
    expect(document.documentElement.getAttribute('data-theme')).toBe('uniops')
    setTheme(null)
    expect(getThemeChoice()).toBeNull()
    expect(document.documentElement.getAttribute('data-theme')).toBe('crm')
  })

  it('unknown ids fall back to uniops (a removed theme cannot strand anyone)', () => {
    setSystemTheme('no-such-theme')
    expect(getTheme()).toBe(DEFAULT_THEME)
    setTheme('gone')
    expect(getTheme()).toBe(DEFAULT_THEME)
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('an empty system value from an old server changes nothing', () => {
    setSystemTheme('crm')
    setSystemTheme(undefined)
    expect(getSystemTheme()).toBe('crm')
  })

  it('useTheme re-renders with theme / choice / system', () => {
    function Probe() { const s = useTheme(); return <span data-testid="t">{`${s.theme}|${s.choice}|${s.system}`}</span> }
    render(<Probe />)
    act(() => setSystemTheme('crm'))
    expect(screen.getByTestId('t').textContent).toBe('crm|null|crm')
    act(() => setTheme('uniops'))
    expect(screen.getByTestId('t').textContent).toBe('uniops|uniops|crm')
  })
})

describe('branding fetch carries the system default', () => {
  it('loadDisplayTimeZone adopts default_theme from the public branding payload', async () => {
    const orig = globalThis.fetch
    globalThis.fetch = (async () => new Response(JSON.stringify({ timezone: 'America/Toronto', default_theme: 'crm' }))) as typeof fetch
    try {
      await loadDisplayTimeZone('/branding')
      expect(getSystemTheme()).toBe('crm')
      expect(document.documentElement.getAttribute('data-theme')).toBe('crm')
    } finally { globalThis.fetch = orig }
  })
})

describe('UserMenu theme picker', () => {
  it('is hidden while only one theme is registered', () => {
    const list = THEMES as ThemeDefList
    const saved = list.splice(1)
    try {
      render(<MemoryRouter><UserMenu name="Ann Lee" /></MemoryRouter>)
      fireEvent.click(screen.getByRole('button', { name: /Ann Lee/ }))
      expect(screen.queryByRole('group', { name: 'Theme' })).toBeNull()
    } finally { list.push(...saved) }
  })

  it('CRM is registered next to the default', () => {
    expect(THEMES.map((t) => t.id)).toEqual([DEFAULT_THEME, 'crm'])
  })

  it('offers "System default (<name>)" + each theme; picking one sets the own choice', () => {
    setSystemTheme('crm')
    render(<MemoryRouter><UserMenu name="Ann Lee" /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: /Ann Lee/ }))
    const sys = screen.getByRole('menuitemradio', { name: 'System default (CRM)' })
    expect(sys).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'UniOps' }))
    expect(getThemeChoice()).toBe('uniops')
    expect(document.documentElement.getAttribute('data-theme')).toBe('uniops')
    expect(screen.getByRole('menuitemradio', { name: 'UniOps' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'System default (CRM)' }))
    expect(getThemeChoice()).toBeNull()
    expect(document.documentElement.getAttribute('data-theme')).toBe('crm')
  })
})
