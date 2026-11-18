import {
  EmptyState, Section, formatDateTime, queryKeys, Muted, useLookup, QueryState,
} from '@/platform-ui'

/** One entry of a record's audit history (sales-api schemas/audit.py). */
export interface AuditEntry {
  id: number
  at: string
  user_name: string
  action: 'create' | 'update' | 'delete'
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  reason: string | null
}

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

function Changes({ e }: { e: AuditEntry }) {
  if (e.action === 'create') return <Muted as="p">Created</Muted>
  const keys = Object.keys(e.after ?? e.before ?? {})
  return (
    <ul className="text-neutral-600">
      {keys.map((k) => (
        <li key={k} className="break-all"><span className="font-medium">{k}</span>: {show(e.before?.[k])} → {show(e.after?.[k])}</li>
      ))}
    </ul>
  )
}

/** Who changed what and when (FR-OB-53), newest first, for one configuration
 *  record. `version` in the key refreshes it after a save. */
export function AuditHistory({ resource, id, version, load }: {
  resource: string
  id: number
  version?: number
  load: (id: number) => Promise<AuditEntry[]>
}) {
  const q = useLookup([...queryKeys.audit({ basePath: resource }, id), version], () => load(id), { errors: 'inline' })
  return (
    <Section title="History" plain className="text-sm">
      {!q.data ? <QueryState q={q} skeleton="h-16">{() => null}</QueryState> : !q.data.length ? (
        <EmptyState title="No changes recorded" className="py-4" />
      ) : (
        <ol className="flex flex-col gap-2 text-xs">
          {q.data.map((e) => (
            <li key={e.id} className="rounded-md border border-neutral-100 p-2">
              <p className="text-neutral-800">
                {formatDateTime(e.at)} · {e.user_name}
                {e.reason && <span className="text-neutral-400"> · {e.reason}</span>}
              </p>
              <Changes e={e} />
            </li>
          ))}
        </ol>
      )}
    </Section>
  )
}

