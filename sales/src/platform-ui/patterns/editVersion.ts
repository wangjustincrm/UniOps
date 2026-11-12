import { useCallback, useEffect, useRef, useState } from 'react'

/** An edit of a versioned record (2026-11-10): the version is the one the person started from, and
 *  a save sends it, so the server refuses (entity.stale) instead of writing over someone else's
 *  change. When the record changes meanwhile: nothing typed yet → the form takes the new values;
 *  something typed → it is kept, and ChangedElsewhere offers Reload. Never a silent remount. */
export function useEditVersion(current: number | undefined, dirty: boolean, reload: () => void) {
  const [seen, setSeen] = useState(current)
  const changed = current !== undefined && seen !== undefined && current !== seen
  // Nothing typed: take the new version at once (state adjusted while rendering, React's pattern
  // for a value that follows a prop); the effect below then loads its values into the form.
  if (changed && !dirty) setSeen(current)
  const reloadRef = useRef(reload)
  useEffect(() => { reloadRef.current = reload })
  const started = useRef(seen)
  useEffect(() => {
    if (seen === started.current) return
    started.current = seen
    reloadRef.current()
  }, [seen])
  const takeTheirs = useCallback(() => setSeen(current), [current])
  // Our own save: the version it made is the one the form now stands on (no reload: it shows
  // what was saved), so a second save is never sent with the version before it.
  const adopt = useCallback((v: number) => { started.current = v; setSeen(v) }, [])
  return {
    /** The version to send with the save. */
    seen,
    changedElsewhere: changed && dirty,
    takeTheirs,
    adopt,
  }
}
