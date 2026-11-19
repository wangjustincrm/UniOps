/**
 * Theme boot — shared by the Vite plugin (vite-plugin.js) and the runtime
 * (src/theme/theme.ts). Plain JS so the Vite config (Node) can load it.
 *
 * The chosen theme lives in a cookie on the parent domain, so one choice covers
 * every module (epms.…, portal.…, finance.… are different origins and do not
 * share localStorage). On localhost the cookie is host-only, which still covers
 * all dev ports (cookies ignore the port).
 */
export const THEME_COOKIE = 'uniops-theme'
export const DEFAULT_THEME = 'uniops'

/** Inline <head> script: set <html data-theme> before the first paint. */
export const THEME_BOOT_SCRIPT =
  `(function(){try{var m=document.cookie.match(/(?:^|; )${THEME_COOKIE}=([a-z0-9-]+)/);` +
  `document.documentElement.setAttribute('data-theme',m?m[1]:'${DEFAULT_THEME}')}catch(e){}})()`
