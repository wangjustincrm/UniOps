// Leaving for the Portal when there is no usable session — without looping.
//
// 2026-10-30 (dev2): an expired token made Sales and the Portal bounce the
// user between them about once a second. Sales got 401, cleared its token and
// meant to go to the Portal's /logout; but the layout saw "no token" first and
// sent the user to `Portal?returnUrl=…` instead; the Portal, still holding the
// same expired token, handed it straight back; 401 again. Now a 401 marks the
// session expired and every way out goes to /logout (which clears the
// Portal's stale session too); and if the bounces repeat anyway, Sales stops
// and asks the user to sign in again.

const EXPIRED = 'sales-session-expired'
const BOUNCES = 'sales-portal-bounces'
export const EXPIRED_FOR_MS = 60_000
export const MAX_BOUNCES = 3
export const BOUNCE_WINDOW_MS = 30_000

const read = (k: string): string | null => { try { return sessionStorage.getItem(k) } catch { return null } }
const write = (k: string, v: string | null) => {
  try {
    if (v === null) sessionStorage.removeItem(k)
    else sessionStorage.setItem(k, v)
  } catch { /* storage blocked: the loop guard is best effort */ }
}

/** A request was refused (401): this session is over. */
export function markSessionExpired(now = Date.now()): void {
  write(EXPIRED, String(now))
  try {
    localStorage.removeItem('sales-auth')
    localStorage.removeItem('portal-auth')  // a stale handoff copy must not bring the token back
  } catch { /* ignore */ }
}

/** A fresh session arrived from the Portal. */
export function clearSessionMarks(): void {
  write(EXPIRED, null)
  write(BOUNCES, null)
}

export type Exit = { kind: 'go'; url: string } | { kind: 'stop' }

const recentBounces = (now: number): number[] =>
  (JSON.parse(read(BOUNCES) ?? '[]') as number[]).filter((t) => now - t < BOUNCE_WINDOW_MS)

/** Where to send a user without a usable session (reads only): to sign in
 *  again — via /logout when the last session expired, so the Portal does not
 *  hand the same token back — or, after MAX_BOUNCES in BOUNCE_WINDOW_MS,
 *  nowhere: the caller shows "sign in again" instead of looping. */
export function exitToPortal(portalUrl: string, here: string, now = Date.now()): Exit {
  if (recentBounces(now).length >= MAX_BOUNCES) return { kind: 'stop' }
  const expiredAt = Number(read(EXPIRED) ?? 0)
  if (expiredAt && now - expiredAt < EXPIRED_FOR_MS) return { kind: 'go', url: `${portalUrl}/logout` }
  return { kind: 'go', url: `${portalUrl}?returnUrl=${encodeURIComponent(here)}` }
}

/** Leave (counting the bounce). */
export function leaveFor(url: string, now = Date.now()): void {
  write(BOUNCES, JSON.stringify([...recentBounces(now), now]))
  window.location.href = url
}
