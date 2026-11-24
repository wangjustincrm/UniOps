import { EmptyState } from '../primitives/States'
import { Stack } from '../primitives/Layout'
import { Muted, Text } from '../primitives/Text'
import { formatDateTime } from '../primitives/format'
import { queryKeys } from '../resource/queryKeys'
import type { AuditEntry } from '../resource/ResourceClient'
import { QueryState } from './QueryState'
import { Section } from './Section'
import { useLookup } from './useRecord'

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

function Changes({ e }: { e: AuditEntry }) {
  if (e.action === 'create') return <Muted as="p">Created</Muted>
  const keys = Object.keys(e.after ?? e.before ?? {})
  return (
    <Text as="ul" tone="soft">
      {keys.map((k) => (
        <Text key={k} as="li" wrap="break"><Text weight="medium">{k}</Text>: {show(e.before?.[k])} → {show(e.after?.[k])}</Text>
      ))}
    </Text>
  )
}

/** Who changed what and when (FR-OB-53), newest first, for one record of a resource.
 *  `version` in the key refreshes it after a save. */
export function RecordHistory({ resource, id, version }: {
  resource: { basePath: string; history: (id: number) => Promise<AuditEntry[]> }
  id: number
  version?: number
}) {
  const q = useLookup([...queryKeys.audit(resource, id), version], () => resource.history(id), { errors: 'inline' })
  return (
    <Section title="History" plain className="text-sm">
      {!q.data ? <QueryState q={q} skeleton="h-16">{() => null}</QueryState> : !q.data.length ? (
        <EmptyState title="No changes recorded" className="py-4" />
      ) : (
        <Stack as="ol" gap={2} size="xs">
          {q.data.map((e) => (
            <li key={e.id} className="rounded-md border border-neutral-100 p-2">
              <Text as="p" tone="body">
                {formatDateTime(e.at)} · {e.user_name}
                {e.reason && <Text tone="faint"> · {e.reason}</Text>}
              </Text>
              <Changes e={e} />
            </li>
          ))}
        </Stack>
      )}
    </Section>
  )
}

