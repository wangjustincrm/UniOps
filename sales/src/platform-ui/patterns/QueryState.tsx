import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { Skeleton } from '../primitives/shell'
import { cn } from '../lib/cn'
import { ErrorView } from './ErrorMessage'

/** What a read shows where its content goes (2026-11-18), the same on every page: a skeleton while it loads,
 *  the error with Retry if it failed, then `children(data)`. Read with `errors: 'inline'` so the failure is not
 *  also said in the status row. Nothing while the read is off (no id yet). */
export function QueryState<T>({ q, skeleton = 'h-20', errorMessages, children }: {
  q: UseQueryResult<T>
  /** The skeleton's height (a Tailwind class). */
  skeleton?: string
  errorMessages?: Record<string, string>
  children: (data: T) => ReactNode
}) {
  if (q.isLoading) return <Skeleton className={cn('w-full', skeleton)} />
  if (q.isError) return <ErrorView error={q.error} messages={errorMessages} onRetry={() => { void q.refetch() }} />
  if (q.data === undefined) return null
  return <>{children(q.data)}</>
}
