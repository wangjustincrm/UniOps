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
const PREFIX = 'ehs-draft'
// A draft with several photographs is the large case; browsers give a single
// origin around 5MB, so a runaway draft would break saving for every other one.
const MAX_DRAFT_BYTES = 2_000_000
const STALE_AFTER_HOURS = 48

export interface Draft<T = unknown> {
  id: string
  kind: string
  savedAt: string
  data: T
}

function key(kind: string, id: string): string {
  return `${PREFIX}:${kind}:${id}`
}

export function saveDraft<T>(kind: string, id: string, data: T): boolean {
  const draft: Draft<T> = { id, kind, savedAt: new Date().toISOString(), data }
  const payload = JSON.stringify(draft)
  if (payload.length > MAX_DRAFT_BYTES) return false
  try {
    localStorage.setItem(key(kind, id), payload)
    return true
  } catch {
    // Quota exceeded, or storage disabled. The caller warns rather than
    // pretending the draft was kept.
    return false
  }
}

export function loadDraft<T>(kind: string, id: string): Draft<T> | null {
  try {
    const raw = localStorage.getItem(key(kind, id))
    return raw ? (JSON.parse(raw) as Draft<T>) : null
  } catch {
    return null
  }
}

export function discardDraft(kind: string, id: string): void {
  try {
    localStorage.removeItem(key(kind, id))
  } catch {
    /* nothing to do */
  }
}

export function listDrafts(kind?: string): Draft[] {
  const out: Draft[] = []
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (!k?.startsWith(`${PREFIX}:`)) continue
      if (kind && !k.startsWith(`${PREFIX}:${kind}:`)) continue
      const raw = localStorage.getItem(k)
      if (raw) out.push(JSON.parse(raw) as Draft)
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
