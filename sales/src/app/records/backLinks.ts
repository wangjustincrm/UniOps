// Where each open record tab was opened from (its "← Back to …"): the tab that was active when a reference was
// followed, by the record's address. Forgotten when the record's tab closes, so a record reached later from nowhere
// (a typed address) goes back to its list.

export interface BackTarget { path: string; title: string }

const backs = new Map<string, BackTarget>()

export function rememberBack(recordPath: string, from: BackTarget): void {
  if (from.path !== recordPath) backs.set(recordPath, from)
}

export function backOf(recordPath: string): BackTarget | undefined {
  return backs.get(recordPath)
}

/** Forget the records whose tab is no longer open. */
export function forgetClosed(isOpen: (recordPath: string) => boolean): void {
  for (const path of [...backs.keys()]) if (!isOpen(path)) backs.delete(path)
}
