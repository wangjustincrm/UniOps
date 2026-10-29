import { useState } from 'react'

/** A draft that follows `value` whenever `value` changes from outside (React's
 *  "adjust state while rendering" pattern — no effect, no extra render pass). */
export function useSynced(value: string): [string, (v: string) => void] {
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  if (seen !== value) {
    setSeen(value)
    setDraft(value)
  }
  return [draft, setDraft]
}
