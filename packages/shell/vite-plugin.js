/**
 * Vite plugin: inject the theme boot script as the first thing in <head>, so the
 * page paints in the user's theme (no flash of the default theme).
 *
 *   // vite.config.ts
 *   import { uniopsTheme } from '@uniops/shell/vite'
 *   plugins: [react(), tailwindcss(), uniopsTheme()]
 */
import { THEME_BOOT_SCRIPT } from './theme-boot.js'

export function uniopsTheme() {
  return {
    name: 'uniops-theme-boot',
    transformIndexHtml() {
      return [{ tag: 'script', children: THEME_BOOT_SCRIPT, injectTo: 'head-prepend' }]
    },
  }
}
