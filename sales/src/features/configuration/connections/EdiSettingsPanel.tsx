import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ErrorMessage, FormField, NumberInput, Select, Skeleton, useCan } from '@/platform-ui'
import { connectionsClient, fetchEdiSettings, saveEdiSettings, type EdiSettings } from './connectionsApi'

const KEY = ['sales', 'config', 'edi-settings']
const errorMessages: Record<string, string> = {
  'edi.connection': 'Choose an active SFTP connection.',
  'auth.forbidden': 'You do not have permission to change the EDI settings.',
}

function Form({ saved, options }: { saved: EdiSettings; options: { value: string; label: string }[] }) {
  const qc = useQueryClient()
  const canWrite = useCan('sales.config')
  const [conn, setConn] = useState<string | null>(saved.connection_id == null ? null : String(saved.connection_id))
  const [minutes, setMinutes] = useState(String(saved.ack_timeout_minutes))
  const m = useMutation({
    mutationFn: () => saveEdiSettings({ connection_id: conn == null ? null : Number(conn), ack_timeout_minutes: Number(minutes) }),
    onSuccess: (r) => qc.setQueryData(KEY, r),
  })
  const valid = Number.isInteger(Number(minutes)) && Number(minutes) >= 1
  const dirty = conn !== (saved.connection_id == null ? null : String(saved.connection_id)) || minutes !== String(saved.ack_timeout_minutes)
  return (
    <div className="flex flex-wrap items-end gap-3">
      <FormField label="Warehouse connection" htmlFor="edi-conn" hint="940s go to its outbound folder (TEST mode: its test outbound folder); 997s are read from it. None: no 940 is made.">
        <Select id="edi-conn" value={conn} onChange={setConn} options={options} emptyLabel="None" disabled={!canWrite} className="w-64" />
      </FormField>
      <FormField label="997 wait (minutes)" htmlFor="edi-wait" hint="No 997 in this time: Timeout and a task.">
        <NumberInput id="edi-wait" value={minutes} min={1} onChange={(e) => setMinutes(e.target.value)} disabled={!canWrite} className="w-28" />
      </FormField>
      <ActionButton size="sm" permission="sales.config" disabled={!dirty || !valid} onAction={() => m.mutateAsync().catch(() => {})}>Save</ActionButton>
      <ErrorMessage error={m.error} messages={errorMessages} />
    </div>
  )
}

/** P4: which SFTP connection is the warehouse's for 940s and 997s, and how
 *  long to wait for a 997 (FR-OB-23). sales.config to change; audited. */
export function EdiSettingsPanel() {
  const settings = useQuery({ queryKey: KEY, queryFn: fetchEdiSettings })
  const conns = useQuery({
    queryKey: [...KEY, 'sftp-connections'],
    queryFn: () => connectionsClient.list({ page: 1, pageSize: 100, filters: [{ field: 'kind', op: 'eq', value: 'SFTP' }, { field: 'active', op: 'eq', value: 'true' }], sort: [{ field: 'name' }] }),
  })
  return (
    <section aria-label="Warehouse EDI" className="flex flex-col gap-2 rounded-md border border-neutral-200 p-3 text-sm">
      <h2 className="font-semibold text-neutral-800">Warehouse EDI (940 / 997)</h2>
      {settings.isLoading || conns.isLoading ? <Skeleton className="h-10 w-full" /> : settings.error || conns.error ? (
        <ErrorMessage error={settings.error ?? conns.error} />
      ) : settings.data && conns.data && (
        <Form key={JSON.stringify(settings.data)} saved={settings.data}
          options={conns.data.items.map((c) => ({ value: String(c.id), label: `${c.name} (${c.mode})` }))} />
      )}
    </section>
  )
}
