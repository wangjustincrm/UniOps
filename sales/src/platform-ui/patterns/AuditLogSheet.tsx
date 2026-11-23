import { useQuery } from '@tanstack/react-query'
import { Collapsible } from '../primitives/Collapsible'
import { DetailList } from '../primitives/DetailList'
import { Sheet } from '../primitives/Sheet'
import { Tabs } from '../primitives/Tabs'
import { Text } from '../primitives/Text'
import { formatDateTime } from '../primitives/format'

/** An audit log as the server gives it (≈ ABP's AuditLog with its actions and entity changes). Any module's. */
export interface AuditLogDetailShape {
  id: string
  kind: string
  name?: string | null
  correlation_id: string
  user_name?: string | null
  role?: string | null
  client_ip?: string | null
  browser_info?: string | null
  http_method?: string | null
  url?: string | null
  route?: string | null
  http_status?: number | null
  execution_time: string
  execution_duration_ms?: number | null
  error_code?: string | null
  exceptions?: string | null
  actions: { id: number; service_name: string; method_name: string; parameters?: unknown; execution_time: string }[]
  changes: EntityChangeShape[]
}
export interface EntityChangeShape {
  id: number; at: string; user_name: string; action: string; entity: string; entity_id?: string | null
  before?: Record<string, unknown> | null; after?: Record<string, unknown> | null; reason?: string | null
}

const json = (v: unknown) => (v === undefined || v === null ? '—' : typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v))
const Pre = ({ children }: { children: string }) => (
  <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-neutral-50 p-2 font-mono text-xs text-neutral-800">{children}</pre>
)

/** ≈ ABP's EntityChange → PropertyChanges: each changed property, before → after (whole values). */
export function EntityChangeDiff({ change }: { change: EntityChangeShape }) {
  const keys = [...new Set([...Object.keys(change.before ?? {}), ...Object.keys(change.after ?? {})])]
    .filter((k) => change.action !== 'update' || json(change.before?.[k]) !== json(change.after?.[k]))
  return (
    <table className="w-full text-xs">
      <thead><tr className="text-left text-neutral-500"><th className="py-1 pr-2 font-medium">Property</th><th className="py-1 pr-2 font-medium">Before</th><th className="py-1 font-medium">After</th></tr></thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k} className="border-t border-neutral-100 align-top">
            <td className="py-1 pr-2 font-medium text-neutral-800">{k}</td>
            <td className="py-1 pr-2 font-mono break-all text-neutral-600">{json(change.before?.[k])}</td>
            <td className="py-1 font-mono break-all text-neutral-900">{json(change.after?.[k])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** One audit log in a sheet (≈ ABP Pro's detail: Overall / Actions / Changes). `load` fetches it by its id. */
export function AuditLogSheet({ id, load, open, onOpenChange }: {
  id: string | null; load: (id: string) => Promise<AuditLogDetailShape>; open: boolean; onOpenChange: (open: boolean) => void
}) {
  const q = useQuery({ queryKey: ['audit-log', id], queryFn: () => load(id!), enabled: open && !!id })
  const d = q.data
  const title = d ? (d.kind === 'request' ? `${d.http_method ?? ''} ${d.route ?? d.url ?? ''}` : `${d.kind}: ${d.name ?? ''}`) : 'Audit log'
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title={title} size="lg"
      description={d ? `${formatDateTime(d.execution_time)} · ${d.user_name ?? 'system'} · correlation ${d.correlation_id}` : undefined}>
      {q.isLoading && <Text tone="soft">Loading…</Text>}
      {q.error != null && <Text tone="danger">Could not load this audit log.</Text>}
      {d && (
        <Tabs ariaLabel="Audit log" defaultValue="overall" items={[
          { value: 'overall', label: 'Overall', content: (
            <div className="flex flex-col gap-3">
              <DetailList items={[
                ['Kind', d.kind], ['User', d.user_name ?? '—'], ['Role', d.role ?? '—'], ['Method', d.http_method ?? '—'],
                ['Route', d.route ?? '—'], ['URL', d.url ?? '—'], ['Status', d.http_status != null ? String(d.http_status) : '—'],
                ['Duration', d.execution_duration_ms != null ? `${d.execution_duration_ms} ms` : '—'], ['Client IP', d.client_ip ?? '—'],
                ['Browser', d.browser_info ?? '—'], ['Error', d.error_code ?? '—'], ['Correlation id', d.correlation_id],
              ]} />
              {d.exceptions && <Collapsible title="Exception" defaultOpen><Pre>{d.exceptions}</Pre></Collapsible>}
            </div>
          ) },
          { value: 'actions', label: 'Actions', count: d.actions.length, content: (
            <div className="flex flex-col gap-2">
              {d.actions.length === 0 && <Text tone="soft">No service action ran.</Text>}
              {d.actions.map((a) => (
                <Collapsible key={a.id} title={`${a.service_name}.${a.method_name}`}><Pre>{json(a.parameters)}</Pre></Collapsible>
              ))}
            </div>
          ) },
          { value: 'changes', label: 'Entity changes', count: d.changes.length, content: (
            <div className="flex flex-col gap-2">
              {d.changes.length === 0 && <Text tone="soft">No record changed.</Text>}
              {d.changes.map((c) => (
                <Collapsible key={c.id} title={`${c.entity} ${c.entity_id ?? ''} · ${c.action}${c.reason ? ` — ${c.reason}` : ''}`}>
                  <EntityChangeDiff change={c} />
                </Collapsible>
              ))}
            </div>
          ) },
        ]} />
      )}
    </Sheet>
  )
}
