/**
 * Theme boot — shared by the Vite plugin (vite-plugin.js) and the runtime
 * (src/theme/theme.ts). Plain JS so the Vite config (Node) can load it.
 *
 * Two cookies on the parent domain (every module is a different origin and
 * they do not share localStorage; on localhost the cookies are host-only,
 * which still covers every dev port):
 *   uniops-theme          the user's own choice (absent = follow the system)
 *   uniops-theme-default  the system default an admin set, cached from the
 *                         public branding endpoint so the next page load can
 *                         paint in it before any request
 * Active theme = own choice ?? system default ?? "uniops".
 */
export const THEME_COOKIE = 'uniops-theme'
export const THEME_DEFAULT_COOKIE = 'uniops-theme-default'
export const DEFAULT_THEME = 'uniops'

/** Inline <head> script: set <html data-theme> before the first paint. */
export const THEME_BOOT_SCRIPT =
  `(function(){try{var c=document.cookie,` +
  `u=c.match(/(?:^|; )${THEME_COOKIE}=([a-z0-9-]+)/),` +
  `d=c.match(/(?:^|; )${THEME_DEFAULT_COOKIE}=([a-z0-9-]+)/);` +
  `document.documentElement.setAttribute('data-theme',u?u[1]:d?d[1]:'${DEFAULT_THEME}')}catch(e){}})()`
