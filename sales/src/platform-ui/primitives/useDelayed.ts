import { useEffect, useState } from 'react'

/** True once `active` has lasted `delay` ms (a quick fetch never flashes a bar). */
export function useDelayed(active: boolean, delay = 200): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setShown(true), delay)
    return () => { clearTimeout(t); setShown(false) }
  }, [active, delay])
  return active && shown
}
