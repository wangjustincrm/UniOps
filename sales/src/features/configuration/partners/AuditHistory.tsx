import { useQuery } from '@tanstack/react-query'
import { EmptyState, ErrorView, Skeleton } from '@/platform-ui'
import { fetchPartnerAudit, type AuditEntry } from './partnersApi'

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

function Changes({ e }: { e: AuditEntry }) {
  if (e.action === 'create') return <p className="text-neutral-500">Created</p>
  const keys = Object.keys(e.after ?? e.before ?? {})
  return (
    <ul className="text-neutral-600">
      {keys.map((k) => (
        <li key={k}><span className="font-medium">{k}</span>: {show(e.before?.[k])} → {show(e.after?.[k])}</li>
      ))}
    </ul>
  )
}

/** Who changed what and when (FR-OB-53), newest first. `version` refreshes it after a save. */
export function AuditHistory({ id, version }: { id: number; version?: number }) {
  const q = useQuery({ queryKey: ['partner-audit', id, version], queryFn: () => fetchPartnerAudit(id) })
  return (
    <section aria-label="History" className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-neutral-800">History</h3>
      {q.isLoading ? <Skeleton className="h-16 w-full" /> : q.error ? <ErrorView error={q.error} /> : !q.data?.length ? (
        <EmptyState title="No changes recorded" className="py-4" />
      ) : (
        <ol className="flex flex-col gap-2 text-xs">
          {q.data.map((e) => (
            <li key={e.id} className="rounded-md border border-neutral-100 p-2">
              <p className="text-neutral-800">
                {new Date(e.at).toLocaleString()} · {e.user_name}
                {e.reason && <span className="text-neutral-400"> · {e.reason}</span>}
              </p>
              <Changes e={e} />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
