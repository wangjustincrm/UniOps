import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { THEME_BOOT_SCRIPT } from '../../theme-boot.js'
import { DEFAULT_THEME, THEMES, getTheme, setTheme, useTheme } from './theme'
import { UserMenu } from '../ui/chrome'

function clearCookie() { document.cookie = 'uniops-theme=; path=/; max-age=0' }

describe('theme boot script', () => {
  afterEach(() => { clearCookie(); document.documentElement.removeAttribute('data-theme') })

  it('sets data-theme from the cookie, or the default without one', () => {
    new Function(THEME_BOOT_SCRIPT)()
    expect(document.documentElement.getAttribute('data-theme')).toBe(DEFAULT_THEME)
    document.cookie = 'uniops-theme=night; path=/'
    new Function(THEME_BOOT_SCRIPT)()
    expect(document.documentElement.getAttribute('data-theme')).toBe('night')
  })
})

describe('setTheme / useTheme', () => {
  afterEach(() => { clearCookie(); document.documentElement.removeAttribute('data-theme') })

  it('applies a known theme to <html>, remembers it in the cookie, sets color-scheme', () => {
    setTheme(DEFAULT_THEME)
    expect(document.documentElement.getAttribute('data-theme')).toBe(DEFAULT_THEME)
    expect(document.documentElement.style.colorScheme).toBe('light')
    expect(document.cookie).toContain(`uniops-theme=${DEFAULT_THEME}`)
  })

  it('an unknown id falls back to the default (a removed theme cannot strand anyone)', () => {
    setTheme('no-such-theme')
    expect(getTheme()).toBe(DEFAULT_THEME)
    document.documentElement.setAttribute('data-theme', 'gone')
    expect(getTheme()).toBe(DEFAULT_THEME)
  })

  it('useTheme re-renders on change', () => {
    function Probe() { const [t] = useTheme(); return <span data-testid="t">{t}</span> }
    render(<Probe />)
    act(() => setTheme(DEFAULT_THEME))
    expect(screen.getByTestId('t').textContent).toBe(DEFAULT_THEME)
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

  it('lists every theme and switches when there are two', () => {
    const list = THEMES as ThemeDefList
    list.push({ id: 'night', label: 'Night', scheme: 'dark' })
    try {
      render(<MemoryRouter><UserMenu name="Ann Lee" /></MemoryRouter>)
      fireEvent.click(screen.getByRole('button', { name: /Ann Lee/ }))
      const night = screen.getByRole('menuitemradio', { name: 'Night' })
      expect(night).toHaveAttribute('aria-checked', 'false')
      fireEvent.click(night)
      expect(document.documentElement.getAttribute('data-theme')).toBe('night')
      expect(document.documentElement.style.colorScheme).toBe('dark')
    } finally {
      list.pop(); clearCookie(); document.documentElement.removeAttribute('data-theme')
    }
  })
})

type ThemeDefList = { id: string; label: string; scheme: 'light' | 'dark' }[]
