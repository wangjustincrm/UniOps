/**
 * Draft saving.
 *
 * The plant has Wi-Fi dead zones — cold storage, the tank area, parts of the
 * yard — and the HSE Manager confirmed a form must be able to be filled in one
 * and submitted on the way out. Drafts therefore live in this browser until
 * they are submitted.
 *
 * This is deliberately not offline sync: no background queue, no conflict
 * merge. It solves the case that actually happens — someone finishes typing
 * where there is no signal — and nothing more. The photographs stay here too,
 * as data URLs, which is why a draft is capped and why one that has sat around
 * for days gets flagged rather than silently kept forever.
 */
import { useEhsAuth } from '@/store/auth'

const PREFIX = 'ehs-draft'

/**
 * Whose drafts these are.
 *
 * ★ Drafts used to be keyed by form alone, which on a shared plant phone meant
 * the next person to sign in was handed whatever the last one was part-way
 * through writing about an injured colleague. Sessions here end by expiring far
 * more often than by anyone pressing Sign out, so clearing on sign-out is not
 * enough on its own — the key itself has to say who wrote it.
 *
 * Portal's session is read as a fallback for the same reason `getToken` does:
 * a deep link can arrive before the handoff has run.
 *
 * ★ Null when nobody can be identified, and never a shared "anonymous" bucket:
 * a bucket everyone falls into when the session has not hydrated yet is the
 * same leak this key was introduced to close, only rarer and so harder to
 * find. Nothing is saved or read in that state.
 */
function currentUserId(): string | null {
  const own = useEhsAuth.getState().user?.id
  if (own) return own
  try {
    const portal = localStorage.getItem('portal-auth')
    const id = portal ? JSON.parse(portal)?.state?.user?.id : null
    if (id) return String(id)
  } catch {
    /* malformed storage is the same as no session */
  }
  return null
}

// Bumped whenever a form's saved shape changes. A draft written by an older
// version is discarded rather than restored: merging half a structure back
// into a form is how a page ends up reading a field that no longer exists,
// which is exactly what happened when "who was hurt" went from a text field
// to a picker and every saved draft started crashing the page.
const SCHEMA_VERSION = 2
// A draft with several photographs is the large case; browsers give a single
// origin around 5MB, so a runaway draft would break saving for every other one.
const MAX_DRAFT_BYTES = 2_000_000
const STALE_AFTER_HOURS = 48

export interface Draft<T = unknown> {
  id: string
  kind: string
  savedAt: string
  version: number
  data: T
}

function key(kind: string, id: string): string | null {
  const user = currentUserId()
  return user ? `${PREFIX}:${user}:${kind}:${id}` : null
}

function mine(storageKey: string): boolean {
  const user = currentUserId()
  return user ? storageKey.startsWith(`${PREFIX}:${user}:`) : false
}

/**
 * Drop drafts written before the key carried a user id.
 *
 * They are unreachable now — nothing looks them up — but unreachable is not
 * gone, and what they hold is the name of somebody who was hurt. Old keys have
 * three colon-separated parts where the current ones have four; neither a user
 * id, a kind nor a draft id contains a colon, so counting them is enough to
 * tell the two apart.
 */
function dropPreUserDrafts(): void {
  try {
    const stale: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith(`${PREFIX}:`) && k.split(':').length === 3) stale.push(k)
    }
    for (const k of stale) localStorage.removeItem(k)
  } catch {
    /* storage disabled — there is nothing to clean up */
  }
}

dropPreUserDrafts()

export function saveDraft<T>(kind: string, id: string, data: T): boolean {
  const draft: Draft<T> = {
    id, kind, savedAt: new Date().toISOString(), version: SCHEMA_VERSION, data,
  }
  const storageKey = key(kind, id)
  // No identified session, nowhere safe to put it. The caller already warns
  // when a draft could not be kept, which is the honest thing to say here too.
  if (!storageKey) return false
  const payload = JSON.stringify(draft)
  if (payload.length > MAX_DRAFT_BYTES) return false
  try {
    localStorage.setItem(storageKey, payload)
    return true
  } catch {
    // Quota exceeded, or storage disabled. The caller warns rather than
    // pretending the draft was kept.
    return false
  }
}

export function loadDraft<T>(kind: string, id: string): Draft<T> | null {
  const storageKey = key(kind, id)
  if (!storageKey) return null
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return null
    const draft = JSON.parse(raw) as Draft<T>
    if (draft.version !== SCHEMA_VERSION) {
      // Written by an older build. Drop it — a stale shape restored into a
      // newer form crashes the page, and a crash is worse than a lost draft.
      discardDraft(kind, id)
      return null
    }
    return draft
  } catch {
    return null
  }
}

export function discardDraft(kind: string, id: string): void {
  const storageKey = key(kind, id)
  if (!storageKey) return
  try {
    localStorage.removeItem(storageKey)
  } catch {
    /* nothing to do */
  }
}

/**
 * Empty this person's drafts on this device.
 *
 * ★ Called when somebody signs out: a draft holds the name of the person hurt
 * and what happened to them, and is worth keeping through a dead zone but not
 * through a shift change. Only their own — somebody else's unsubmitted injury
 * report is not this session's to throw away, and the key already keeps it out
 * of reach.
 */
export function clearMyDrafts(): void {
  try {
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && mine(k)) keys.push(k)
    }
    // Collected first: removing while iterating shifts the indices under it.
    for (const k of keys) localStorage.removeItem(k)
  } catch {
    /* storage disabled — nothing was kept in the first place */
  }
}

export function listDrafts(kind?: string): Draft[] {
  const out: Draft[] = []
  const user = currentUserId()
  if (!user) return out
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (!k || !mine(k)) continue
      if (kind && !k.startsWith(`${PREFIX}:${user}:${kind}:`)) continue
      const raw = localStorage.getItem(k)
      if (!raw) continue
      const draft = JSON.parse(raw) as Draft
      if (draft.version === SCHEMA_VERSION) out.push(draft)
    }
  } catch {
    return out
  }
  return out.sort((a, b) => b.savedAt.localeCompare(a.savedAt))
}

/** Drafts old enough that the phone holding them is a single point of failure. */
export function isStale(draft: Draft): boolean {
  const age = Date.now() - new Date(draft.savedAt).getTime()
  return age > STALE_AFTER_HOURS * 3_600_000
}
