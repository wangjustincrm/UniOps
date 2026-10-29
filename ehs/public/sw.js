/*
 * Safety's service worker — what makes "Add to Home Screen" an app.
 *
 * Round 10, q10-app: "version A sounds reasonable … as long as the view
 * scales properly". It does one thing: when a page cannot be reached at all,
 * it shows a plain "you are offline" page instead of the browser's error.
 *
 * What it deliberately does NOT do:
 *   - cache the app, its scripts, or any page — every page and every update
 *     comes from the server, exactly as in a browser tab, so a new release
 *     reaches the phone on its next open;
 *   - touch API calls, files or anything but page navigations — safety data
 *     is never shown from a stale copy, and nothing with a person's session
 *     in it is stored;
 *   - reach outside this host. Its scope is the Safety origin only; Portal
 *     and the other modules are other hosts and never see it.
 *
 * ★ How to withdraw it, if ever needed: replace this file with
 *
 *     self.addEventListener('install', () => self.skipWaiting())
 *     self.addEventListener('activate', (e) => e.waitUntil(
 *       self.clients.claim()
 *         .then(() => self.registration.unregister())
 *         .then(() => self.clients.matchAll({ type: 'window' }))
 *         .then((all) => Promise.all(all.map((c) => c.navigate(c.url))))))
 *
 *   and remove the register call in src/main.tsx. sw.js is served no-cache
 *   (nginx.conf), so every installed phone fetches it on its next open and
 *   the worker removes itself; from the open after that the page runs with
 *   no worker at all, as a plain website. (Tested against a built image:
 *   registrations 1 → 0, then uncontrolled. Unregistering without claiming
 *   first left the page controlled, which is why claim comes first.)
 */
const CACHE = 'safety-offline-v1'
const OFFLINE = '/offline.html'

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(new Request(OFFLINE, { cache: 'reload' }))))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  // Page navigations only; everything else goes to the network untouched.
  if (event.request.mode !== 'navigate') return
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)))
})
