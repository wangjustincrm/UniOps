import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Circle, TriangleAlert, XCircle } from 'lucide-react'
import {
  ActionButton, Badge, Callout, DateTimeInput, Hint, LocalTable, Section, formatDateTime, queryKeys, type ColumnDef,
} from '@/platform-ui'
import { RunNowButton } from './RunNowButton'
import { connectionHealth } from './connectionHealth'
import { clearAuthLatch, connectionsClient, pinHostKey, resetCursor, testConnection, type ConnectionRead, type ConnectionTest } from './connectionsApi'

const CONFIG = 'sales.config'

type Status = 'done' | 'todo' | 'problem' | 'unknown'
const ICON: Record<Status, ReactNode> = {
  done: <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-success-600" />,
  todo: <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning-600" />,
  problem: <XCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-danger-600" />,
  unknown: <Circle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-neutral-400" />,
}
const WORD: Record<Status, string> = { done: 'Done', todo: 'To do', problem: 'Problem', unknown: 'Not checked' }

/** One step: what it is, whether it is done, the next step, its button right there. */
function Step({ label, status, children, action }: { label: string; status: Status; children: ReactNode; action?: ReactNode }) {
  return (
    <li aria-label={label} data-status={status} className="flex flex-wrap items-start gap-2 py-2">
      {ICON[status]}
      <div className="flex min-w-0 flex-1 basis-56 flex-col">
        <span className="font-medium">{label} <span className="sr-only">({WORD[status]})</span></span>
        <Hint className="break-words">{children}</Hint>
      </div>
      {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
    </li>
  )
}

type FolderRow = ConnectionTest['folders'][number]
const folderColumns: ColumnDef<FolderRow, unknown>[] = [
  { accessorKey: 'folder', header: 'Folder', cell: ({ row }) => <code className="break-all text-xs">{row.original.folder}</code> },
  { accessorKey: 'ok', header: 'Reachable', cell: ({ row: { original: f } }) => f.ok ? <Badge variant="success">Yes</Badge>
    : <span className="text-danger-600">{f.error ?? 'No'}</span> },
  { accessorKey: 'files', header: 'Files', cell: ({ row }) => row.original.files ?? '—' },
]

/** Shopify: the pull cursor, read-only; a person resets it (audited). */
function Cursor({ c, onDone }: { c: ConnectionRead; onDone: () => void }) {
  const [value, setValue] = useState<string | null>(null)  // ISO with its zone
  const reset = useMutation({ mutationFn: (v: string | null) => resetCursor(c.id, v), onSuccess: () => { setValue(null); onDone() } })
  return (
    <div className="flex flex-col gap-1">
      <p>Next pull from: <span className="font-medium">{c.cursor_updated_at ? formatDateTime(c.cursor_updated_at) : 'not set: nothing is pulled'}</span></p>
      <div className="flex flex-wrap items-center gap-2">
        <DateTimeInput aria-label="New cursor" value={value} onChange={setValue} className="h-8 max-w-[14rem] text-xs" />
        <ActionButton size="sm" variant="secondary" permission={CONFIG} disabled={!value}
          done="Cursor reset: the next pass reads from there" onAction={() => reset.mutateAsync(value)}>Reset cursor</ActionButton>
      </div>
    </div>
  )
}

/** A connection's Overview: what it takes to work, step by step, each with its
 *  status, what to do next and the button for it. The test lists folders and
 *  changes nothing; it shows the server's key for pinning (no trust on first use). */
export function ConnectionChecklist({ connection: c }: { connection: ConnectionRead }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const test = useMutation({ mutationFn: () => testConnection(c.id), onSuccess: setResult })
  const pin = useMutation({ mutationFn: (line: string) => pinHostKey(c, line), onSuccess: () => { setResult(null); refresh() } })
  const clear = useMutation({ mutationFn: () => clearAuthLatch(c.id), onSuccess: refresh })
  const sftp = c.kind === 'SFTP'
  const key = result?.host_key
  const testButton = (
    <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => test.mutateAsync()}>Test connection</ActionButton>
  )
  const pinButton = key && !key.matches && (
    <ActionButton size="sm" permission={CONFIG} done="Host key pinned" onAction={() => pin.mutateAsync(key.line)}>
      {key.pinned ? 'Replace the pinned key with this one' : 'Pin this key'}
    </ActionButton>
  )
  const failedFolders = result?.folders.filter((f) => !f.ok).length ?? 0
  const reached: Status = !result ? 'unknown' : result.error || failedFolders ? 'problem' : 'done'
  const health = connectionHealth(c)
  return (
    <Section title="Set-up" label="Connection status" plain className="gap-3 text-sm">
      <ol aria-label="Set-up checklist" className="flex flex-col divide-y divide-neutral-100">
        <Step label="Secret on server" status={c.secret_present ? 'done' : 'todo'}>
          {c.secret_present ? <>Found on the server under the name <code>{c.secret_name}</code>.</>
            : c.secret_name ? <>Ask your administrator to add the password on the server under the name <code>{c.secret_name}</code>.</>
              : 'Give this connection the name of its password on the server, in Settings.'}
        </Step>
        {sftp && (
          <Step label="Host key pinned" status={c.host_key && !(key && !key.matches) ? 'done' : key?.pinned ? 'problem' : 'todo'}
            action={<>{!c.host_key && !key && testButton}{pinButton}</>}>
            {key && !key.matches ? <>Check this fingerprint with the server's owner, then pin it: <code className="break-all">{key.fingerprint}</code></>
              : c.host_key ? 'The server is trusted.' : 'Test the connection to see the server\'s key, then pin it.'}
          </Step>
        )}
        <Step label="Login OK" status={c.auth_failed_at ? 'problem' : 'done'}
          action={c.auth_failed_at && (
            <ActionButton size="sm" permission={CONFIG} done="Cleared: it is tried again on the next cycle" onAction={() => clear.mutateAsync()}>
              Clear and try again next cycle
            </ActionButton>
          )}>
          {c.auth_failed_at ? `Refused, and skipped since ${formatDateTime(c.auth_failed_at)}: ${c.auth_failed_reason ?? 'no reason given'}. Fix the login, then clear this.`
            : 'No refusal recorded.'}
        </Step>
        <Step label={sftp ? 'Folders reachable' : 'Store reachable'} status={reached} action={(!sftp || c.host_key) && testButton}>
          {!result ? (sftp && !c.host_key ? 'Pin the key first, then the test lists the folders.' : 'Test the connection to check.')
            : reached === 'done' ? 'The test reached everything.' : (result.error ?? `${failedFolders} folder(s) could not be reached.`)}
        </Step>
        <Step label="Last run" action={<RunNowButton connection={c} variant="secondary" />}
          status={!c.last_run_at ? 'unknown' : c.last_run_status === 'FAILED' ? 'problem' : c.last_run_status === 'PARTIAL' ? 'todo' : 'done'}>
          {!c.last_run_at ? 'Not run yet.'
            : `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}`}
        </Step>
      </ol>
      {health.state === 'inactive' && <Callout tone="neutral">{health.tip}</Callout>}
      <Hint as="p">
        {sftp ? 'The test lists the folders. Nothing is created, moved or deleted.' : "The test reads the store's name and currency. Nothing is written."}
      </Hint>
      {result?.shop && (
        <p>Store: <span className="font-medium">{result.shop.name}</span> · {result.shop.myshopify_domain ?? result.shop.domain} · {result.shop.currency}</p>
      )}
      {result && result.folders.length > 0 && (
        <LocalTable<FolderRow> ariaLabel="Test result" columns={folderColumns} rows={result.folders} rowKey={(f) => f.folder} />
      )}
      {c.kind === 'SHOPIFY' && <Cursor c={c} onDone={refresh} />}
    </Section>
  )
}
