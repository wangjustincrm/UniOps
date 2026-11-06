// A "create, then submit" page whose submit half is refused has still saved
// the document, as a draft. The page goes to that draft instead of staying on
// the form — staying meant every further click saved another draft
// (ISS-0090, ISS-0166) — and the draft's page shows why it was not submitted.
//
// In memory on purpose: the reason only has to survive the in-app navigation
// to the draft, not a reload.
const pending = new Map<string, string>()

export function rememberSubmitFailure(docId: string, message: string): void {
  pending.set(docId, message)
}

/** The refusal waiting for this document, if any. Read in a state initializer;
 *  clear it with clearSubmitFailure once the page has mounted. */
export function peekSubmitFailure(docId: string | undefined): string | null {
  return docId ? pending.get(docId) ?? null : null
}

export function clearSubmitFailure(docId: string | undefined): void {
  if (docId) pending.delete(docId)
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}
