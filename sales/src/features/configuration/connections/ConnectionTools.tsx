import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Badge, DateTimeInput, Hint, Section, formatDateTime, queryKeys, Callout } from '@/platform-ui'
import { clearAuthLatch, connectionsClient, pinHostKey, resetCursor, testConnection, type ConnectionRead, type ConnectionTest } from './connectionsApi'

const CONFIG = 'sales.config'

/** Shopify: the pull cursor, read-only; a person resets it (audited, FR-OB-10). */
function Cursor({ c, onDone }: { c: ConnectionRead; onDone: () => void }) {
  const [value, setValue] = useState<string | null>(null)  // ISO with its zone
  const reset = useMutation({ mutationFn: (v: string | null) => resetCursor(c.id, v), onSuccess: () => { setValue(null); onDone() } })
  return (
    <div className="flex flex-col gap-1">
      <p>Next pull from: <span className="font-medium">{c.cursor_updated_at ? formatDateTime(c.cursor_updated_at) : 'not set — nothing is pulled'}</span></p>
      <div className="flex flex-wrap items-center gap-2">
        <DateTimeInput aria-label="New cursor" value={value} onChange={setValue} className="h-8 max-w-[14rem] text-xs" />
        <ActionButton size="sm" variant="secondary" permission={CONFIG} disabled={!value}
          done="Cursor reset: the next pass reads from there" onAction={() => reset.mutateAsync(value)}>Reset cursor</ActionButton>
      </div>
    </div>
  )
}

/** Under a connection's form: whether its secret is on the server, its login
 *  latch, and the connection test (FR-OB-74: lists folders, changes nothing),
 *  which shows the server's host key for pinning (no trust on first use). */
export function ConnectionTools({ connection: c }: { connection: ConnectionRead }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const test = useMutation({ mutationFn: () => testConnection(c.id), onSuccess: setResult })
  const pin = useMutation({ mutationFn: (line: string) => pinHostKey(c, line), onSuccess: () => { setResult(null); refresh() } })
  const clear = useMutation({ mutationFn: () => clearAuthLatch(c.id), onSuccess: refresh })

  return (
    <Section title="Status" label="Connection status" plain className="gap-3 text-sm">
      <p>
        Secret <code className="text-xs">{c.secret_name ?? '—'}</code>:{' '}
        <Badge variant={c.secret_present ? 'success' : 'warning'}>{c.secret_present ? 'present' : 'missing'}</Badge>
      </p>
      {c.kind === 'SFTP' && (
        <p>Host key: {c.host_key ? <Badge variant="success">pinned</Badge> : <Badge variant="warning">not pinned</Badge>}</p>
      )}
      {c.auth_failed_at && (
        <Callout tone="danger" title={`Login refused — skipped since ${formatDateTime(c.auth_failed_at)}`}>
          <p className="text-xs">{c.auth_failed_reason}</p>
          <ActionButton size="sm" className="mt-2" permission={CONFIG} done="Cleared: it is tried again on the next cycle" onAction={() => clear.mutateAsync()}>
            Clear and try again next cycle
          </ActionButton>
        </Callout>
      )}
      {c.kind === 'SHOPIFY' && <Cursor c={c} onDone={refresh} />}
      <div>
        <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => test.mutateAsync()}>
          Test connection
        </ActionButton>
        <Hint as="p" className="mt-1">
          {c.kind === 'SFTP' ? 'Lists the folders. Nothing is created, moved or deleted.' : "Reads the store's name and currency. Nothing is written."}
        </Hint>
      </div>
      {result && (
        <div className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2">
          {result.host_key && (
            <div>
              <p>Server host key: <code className="break-all text-xs">{result.host_key.fingerprint}</code></p>
              {!result.host_key.matches && (
                <ActionButton size="sm" className="mt-1" permission={CONFIG} done="Host key pinned" onAction={() => pin.mutateAsync(result.host_key!.line)}>
                  {result.host_key.pinned ? 'Replace the pinned key with this one' : 'Pin this key'}
                </ActionButton>
              )}
            </div>
          )}
          {result.shop && (
            <p>Store: <span className="font-medium">{result.shop.name}</span> · {result.shop.myshopify_domain ?? result.shop.domain} · {result.shop.currency}</p>
          )}
          {result.error && <p className="text-danger-600">{result.error}</p>}
          {result.folders.length > 0 && (
            <ul className="text-xs">
              {result.folders.map((f) => (
                <li key={f.folder}>
                  <code>{f.folder}</code>: {f.ok ? `${f.files} file(s)` : <span className="text-danger-600">{f.error}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  )
}
