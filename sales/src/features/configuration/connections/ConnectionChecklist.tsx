import { useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Circle, TriangleAlert, XCircle } from 'lucide-react'
import {
  ActionButton, Badge, Callout, ConfirmAction, DateTimeInput, Hint, LocalTable, Section, ask, formatDateTime, queryKeys, type ColumnDef, Cluster, Stack, Text, Icon,
} from '@/platform-ui'
import { RunNowButton } from './RunNowButton'
import { connectionHealth } from './connectionHealth'
import { useEdiSettings } from './connectionSettings'
import { clearAuthLatch, connectionsClient, gatewayOf, pinHostKey, resetCursor, testConnection, type ConnectionRead, type ConnectionTest } from './connectionsApi'

const CONFIG = 'sales.config'
const TICK_ERRORS: Record<string, string> = {
  'connection.go_live_gate': 'Writing can be turned on once every go-live notification has a recipient and was tested: Configuration → Notifications (Health lists what is missing).',
  'connection.field_required': 'Set the folder first, in Settings.',
  'entity.stale': 'Someone else changed this connection. Refresh and try again.',
}

type Status = 'done' | 'todo' | 'problem' | 'unknown'
const ICON: Record<Status, ReactNode> = {
  done: <Icon icon={CheckCircle2} className="mt-0.5 text-success-600" />,
  todo: <Icon icon={TriangleAlert} className="mt-0.5 text-warning-600" />,
  problem: <Icon icon={XCircle} tone="danger" className="mt-0.5" />,
  unknown: <Icon icon={Circle} tone="faint" className="mt-0.5" />,
}
const WORD: Record<Status, string> = { done: 'Done', todo: 'To do', problem: 'Problem', unknown: 'Not checked' }

/** One step: what it is, whether it is done, the next step, its button right there. */
function Step({ label, status, children, action }: { label: string; status: Status; children: ReactNode; action?: ReactNode }) {
  return (
    <Cluster aria-label={label} data-status={status} as="li" gap={2} align="start" className="py-2">
      {ICON[status]}
      <Stack gap={0} className="flex-1 basis-56">
        <Text weight="medium">{label} <span className="sr-only">({WORD[status]})</span></Text>
        <Hint className="break-words">{children}</Hint>
      </Stack>
      {action && <Cluster gap={2} align="center">{action}</Cluster>}
    </Cluster>
  )
}

type FolderRow = ConnectionTest['folders'][number]
const folderColumns: ColumnDef<FolderRow, unknown>[] = [
  { accessorKey: 'folder', header: 'Folder', cell: ({ row }) => <Text as="code" size="xs" wrap="break">{row.original.folder}</Text> },
  { accessorKey: 'ok', header: 'Reachable', cell: ({ row: { original: f } }) => f.ok ? <Badge variant="success">Yes</Badge>
    : <Text tone="danger">{f.error ?? 'No'}</Text> },
  { accessorKey: 'files', header: 'Files', cell: ({ row }) => row.original.files ?? '—' },
]

/** Shopify / Authorize.Net: the pull cursor, read-only; a person resets it (audited). */
function Cursor({ c, onDone }: { c: ConnectionRead; onDone: () => void }) {
  const [value, setValue] = useState<string | null>(null)  // ISO with its zone
  return (
    <Stack gap={1}>
      <p>{gatewayOf(c.kind) ? `Next read: ${gatewayOf(c.kind)!.cursorWhat} ` : 'Next pull from: '}<Text weight="medium">{c.cursor_updated_at ? formatDateTime(c.cursor_updated_at) : 'not set: nothing is pulled'}</Text></p>
      {gatewayOf(c.kind) && !c.cursor_updated_at && <Hint>To read the history, set it to the first day you want (e.g. Jun 1, 2024).</Hint>}
      <Cluster gap={2} align="center">
        <DateTimeInput aria-label="New cursor" value={value} onChange={setValue} className="h-8 max-w-[14rem] text-xs" />
        <ActionButton size="sm" variant="secondary" permission={CONFIG} disabled={!value} disabledReason="Choose the new date and time first."
          done="Cursor reset: the next pass reads from there" after={() => { setValue(null); onDone() }} onAction={() => resetCursor(c.id, value)}>Reset cursor</ActionButton>
      </Cluster>
    </Stack>
  )
}

/** A connection's Overview: what it takes to work, step by step, each with its
 *  status, what to do next and the button for it. The test lists folders and
 *  changes nothing; it shows the server's key for pinning (no trust on first use). */
export function ConnectionChecklist({ connection: c }: { connection: ConnectionRead }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const changed = [queryKeys.list(connectionsClient)]  // what a change here reads again
  // Read / Write on or off right here, after a question that says what it will do (user 2026-11-12).
  const [writing, setWriting] = useState(false)  // turning Write on asks why (user 2026-11-24)
  const doesOf = (which: 'can_read' | 'can_write') => {
    const sftpFolder = which === 'can_read' ? c.inbound_folder : c.outbound_folder
    const what = which === 'can_read'
      ? (sftp ? `Sales will read the files in ${sftpFolder ?? '(no read folder)'}` : gatewayOf(c.kind) ? gatewayOf(c.kind)!.reads : "Sales will pull the store's orders")
      : (sftp ? `Sales will put the files it sends (940, 943, 810) in ${sftpFolder ?? '(no write folder)'}`
        + (c.after_read !== 'LEAVE' ? ` and ${c.after_read === 'MOVE' ? `move what it reads to ${c.processed_folder}` : 'delete what it reads'}` : '')
        : 'Sales will send fulfilments and tracking to the store and close orders')
    return `${what}. This is real work on the partner's side.`
  }
  const toggle = async (which: 'can_read' | 'can_write') => {
    const turnOn = !c[which]
    if (turnOn && which === 'can_write') { setWriting(true); return null }
    const ok = await ask(turnOn
      ? { title: `Turn reading on for ${c.name}?`, description: doesOf(which), confirmLabel: 'Turn on' }
      : { title: `Turn ${which === 'can_read' ? 'reading' : 'writing'} off for ${c.name}?`,
        description: which === 'can_read' ? 'Nothing will be read from this connection.' : 'Nothing will be written; what it would send is kept, not sent (Suppressed).',
        confirmLabel: 'Turn off' })
    if (!ok) return null
    return connectionsClient.update(c.id, which === 'can_read' ? { can_read: turnOn } : { can_write: turnOn }, c.version)
  }
  const tickButton = (which: 'can_read' | 'can_write') => (
    <ActionButton size="sm" variant="secondary" permission={CONFIG} errorMessages={TICK_ERRORS}
      done={(r) => (r ? `${which === 'can_read' ? 'Reading' : 'Writing'} turned ${c[which] ? 'off' : 'on'}` : null)}
      invalidates={changed} onAction={() => toggle(which)}>
      {c[which] ? 'Turn off' : 'Turn on'}
    </ActionButton>
  )
  const sftp = c.kind === 'SFTP'
  const gateway = gatewayOf(c.kind)  // a payment gateway: no folders, no host key, reads only
  const anet = !!gateway
  const key = result?.host_key
  const testButton = (
    <ActionButton size="sm" variant="secondary" permission={CONFIG} refreshes={false} after={(r) => setResult(r as ConnectionTest)} onAction={() => testConnection(c.id)}>Test connection</ActionButton>
  )
  const pinButton = key && !key.matches && (
    <ActionButton size="sm" permission={CONFIG} done="Host key pinned" invalidates={changed} after={() => setResult(null)} onAction={() => pinHostKey(c, key.line)}>
      {key.pinned ? 'Replace the pinned key with this one' : 'Pin this key'}
    </ActionButton>
  )
  const failedFolders = result?.folders.filter((f) => !f.ok).length ?? 0
  const reached: Status = !result ? 'unknown' : result.error || failedFolders ? 'problem' : 'done'
  const health = connectionHealth(c)
  // What it is used for: with writing off, what it sends is kept, not sent (Suppressed).
  const edi = useEdiSettings().settings
  const usedFor = [edi?.connection_id === c.id && 'warehouse connection', edi?.invoice_connection_id === c.id && '810 connection'].filter(Boolean).join(' and the ')
  const moves = sftp && c.after_read !== 'LEAVE'
  return (
    <Section title="Set-up" label="Connection status" plain className="gap-3 text-sm">
      <Stack aria-label="Set-up checklist" as="ol" gap={0} className="divide-y divide-neutral-100">
        <Step label="Secret on server" status={c.secret_present ? 'done' : 'todo'}>
          {c.secret_present ? <>Found on the server under the name <code>{c.secret_name}</code>.</>
            : c.secret_name ? <>Ask your administrator to add the password on the server under the name <code>{c.secret_name}</code>.</>
              : 'Give this connection the name of its password on the server, in Settings.'}
        </Step>
        {sftp && (
          <Step label="Host key pinned" status={c.host_key && !(key && !key.matches) ? 'done' : key?.pinned ? 'problem' : 'todo'}
            action={<>{!c.host_key && !key && testButton}{pinButton}</>}>
            {key && !key.matches ? <>Check this fingerprint with the server's owner, then pin it: <Text as="code" wrap="break">{key.fingerprint}</Text></>
              : c.host_key ? 'The server is trusted.' : 'Test the connection to see the server\'s key, then pin it.'}
          </Step>
        )}
        <Step label="Login OK" status={c.auth_failed_at ? 'problem' : 'done'}
          action={c.auth_failed_at && (
            <ActionButton size="sm" permission={CONFIG} done="Cleared: it is tried again on the next cycle" invalidates={changed} onAction={() => clearAuthLatch(c.id)}>
              Clear and try again next cycle
            </ActionButton>
          )}>
          {c.auth_failed_at ? `Refused, and skipped since ${formatDateTime(c.auth_failed_at)}: ${c.auth_failed_reason ?? 'no reason given'}. Fix the login, then clear this.`
            : 'No refusal recorded.'}
        </Step>
        <Step label={sftp ? 'Folders reachable' : anet ? `${gateway!.label} reachable` : 'Store reachable'} status={reached} action={(!sftp || c.host_key) && testButton}>
          {!result ? (sftp && !c.host_key ? 'Pin the key first, then the test lists the folders.' : 'Test the connection to check.')
            : reached === 'done' ? 'The test reached everything.' : (result.error ?? `${failedFolders} folder(s) could not be reached.`)}
        </Step>
        <Step label="Reads" status={c.can_read ? 'done' : 'todo'} action={tickButton('can_read')}>
          {!c.can_read ? 'Read is off: nothing is read.'
            : sftp ? <>From <code>{c.inbound_folder}</code>
              {moves ? (c.can_write
                ? <>; after reading, {c.after_read === 'MOVE' ? <>moved to <code>{c.processed_folder}</code></> : 'deleted'}</>
                : <>; after reading, left in place (moving and deleting need Write)</>)
                : <>; after reading, left in place</>}
              {c.error_folder && c.can_write ? <>; a file that cannot be read goes to <code>{c.error_folder}</code></> : null}.</>
              : anet ? `${gateway!.reads.replace(/^Sales reads /, '')} (Payments, Settlements).` : "The store's orders."}
        </Step>
        {!anet && <Step label="Writes" status={c.can_write ? 'done' : usedFor ? 'todo' : 'unknown'} action={tickButton('can_write')}>
          {c.can_write
            ? (sftp ? <>Into <code>{c.outbound_folder}</code>{usedFor ? ` (the ${usedFor})` : ''}. The server must also allow this address.</> : 'Fulfilments and tracking to the store, and closing orders.')
            : usedFor ? <>Write is off: this is the {usedFor}, so what it sends is kept and not sent (Suppressed).</>
              : 'Write is off: nothing is written.'}
        </Step>}
        <Step label="Last run" action={<RunNowButton connection={c} variant="secondary" />}
          status={!c.last_run_at ? 'unknown' : c.last_run_status === 'FAILED' ? 'problem' : c.last_run_status === 'PARTIAL' ? 'todo' : 'done'}>
          {!c.last_run_at ? 'Not run yet.'
            : `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}`}
        </Step>
      </Stack>
      {health.state === 'inactive' && <Callout tone="neutral">{health.tip}</Callout>}
      <Hint as="p">
        {sftp ? 'The test lists the folders. Nothing is created, moved or deleted.'
          : anet ? 'The test only checks the login. Sales never charges, pays, refunds or voids anything.'
            : "The test reads the store's name and currency. Nothing is written."}
      </Hint>
      {result?.shop && (
        <p>Store: <Text weight="medium">{result.shop.name}</Text> · {result.shop.myshopify_domain ?? result.shop.domain} · {result.shop.currency}</p>
      )}
      {result?.gateway && <p>Authorize.Net: <Text weight="medium">{result.gateway.host}</Text> · login {result.gateway.login}</p>}
      {result && result.folders.length > 0 && (
        <LocalTable<FolderRow> ariaLabel="Test result" columns={folderColumns} rows={result.folders} rowKey={(f) => f.folder} />
      )}
      {(c.kind === 'SHOPIFY' || anet) && <Cursor c={c} onDone={refresh} />}
      <ConfirmAction open={writing} onOpenChange={setWriting} title={`Turn writing on for ${c.name}?`} description={doesOf('can_write')} destructive
        confirmLabel="Turn on" reason={{ label: 'Why it is turned on' }} errorMessages={TICK_ERRORS} done="Writing turned on" invalidates={changed}
        onConfirm={(why) => connectionsClient.update(c.id, { can_write: true, reason: why }, c.version)} />
    </Section>
  )
}
