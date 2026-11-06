// A <Link> into the Trace page that primes an already-open tab for that
// material first (see traceNav.ts).
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { traceHref, usePrimeTraceTab } from './traceNav'

export function TraceLink({ material, batch, date, className, title, children }: {
  material: string
  batch?: string | null
  date?: string | null
  className?: string
  title?: string
  children: ReactNode
}) {
  const prime = usePrimeTraceTab()
  const href = traceHref(material, { batch, date })
  return (
    <Link to={href} className={className} title={title} onClick={() => prime(material, href)}>
      {children}
    </Link>
  )
}
