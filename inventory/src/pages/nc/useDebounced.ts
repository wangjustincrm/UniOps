import { useEffect, useState } from 'react'

/** Debounced copy of a value: the search box drives server queries, and one
 *  request per keystroke would flicker through half-typed answers. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
}
