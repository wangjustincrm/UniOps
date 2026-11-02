import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Badge, DateTimeInput, ErrorMessage, formatDateTime, queryKeys } from '@/platform-ui'
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
          onAction={() => reset.mutateAsync(value).catch(() => {})}>Reset cursor</ActionButton>
      </div>
      <ErrorMessage error={reset.error} />
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
    <section aria-label="Connection status" className="flex flex-col gap-3 text-sm">
      <h3 className="font-semibold text-neutral-800">Status</h3>
      <p>
        Secret <code className="text-xs">{c.secret_name ?? '—'}</code>:{' '}
        <Badge variant={c.secret_present ? 'success' : 'warning'}>{c.secret_present ? 'present' : 'missing'}</Badge>
      </p>
      {c.kind === 'SFTP' && (
        <p>Host key: {c.host_key ? <Badge variant="success">pinned</Badge> : <Badge variant="warning">not pinned</Badge>}</p>
      )}
      {c.auth_failed_at && (
        <div role="alert" className="rounded-md border border-danger-200 bg-danger-50 p-2">
          <p className="font-medium text-danger-700">Login refused — skipped since {formatDateTime(c.auth_failed_at)}</p>
          <p className="text-xs text-danger-700">{c.auth_failed_reason}</p>
          <ActionButton size="sm" className="mt-2" permission={CONFIG} onAction={() => clear.mutateAsync().catch(() => {})}>
            Clear and try again next cycle
          </ActionButton>
        </div>
      )}
      {c.kind === 'SHOPIFY' && <Cursor c={c} onDone={refresh} />}
      <div>
        <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => test.mutateAsync().catch(() => {})}>
          Test connection
        </ActionButton>
        <p className="mt-1 text-xs text-neutral-500">
          {c.kind === 'SFTP' ? 'Lists the folders. Nothing is created, moved or deleted.' : "Reads the store's name and currency. Nothing is written."}
        </p>
      </div>
      <ErrorMessage error={test.error ?? pin.error ?? clear.error} />
      {result && (
        <div className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2">
          {result.host_key && (
            <div>
              <p>Server host key: <code className="break-all text-xs">{result.host_key.fingerprint}</code></p>
              {!result.host_key.matches && (
                <ActionButton size="sm" className="mt-1" permission={CONFIG} onAction={() => pin.mutateAsync(result.host_key!.line).catch(() => {})}>
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
    </section>
  )
}
