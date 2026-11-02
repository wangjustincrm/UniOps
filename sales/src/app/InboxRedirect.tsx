import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { ErrorView, Skeleton, requestOpen } from '@/platform-ui'
import { resolveInbox } from '@/features/health/healthApi'

/** `/inbox/<problem key>` — where every Sales task in the Task Inbox (epms,
 *  Portal) links to: asks sales-api which page and record the task is about,
 *  asks that list to open the record, then goes there. */
export function InboxRedirect({ taskKey }: { taskKey: string }) {
  const [to, setTo] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let live = true
    resolveInbox(taskKey).then((t) => {
      if (!live) return
      if (t.resource && t.id != null) requestOpen(t.resource, t.id)
      setTo(t.page || '/home')
    }, (e: unknown) => { if (live) setError(e) })
    return () => { live = false }
  }, [taskKey])
  if (error) return <div className="p-6"><ErrorView error={error} /></div>
  if (!to) return <div className="p-6"><Skeleton className="h-10 w-80" /></div>
  return <Navigate to={to} replace />
}
