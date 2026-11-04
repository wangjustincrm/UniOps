import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { ActionButton, DetailList, ErrorMessage, SchemaForm, FormSheet, Section, Skeleton, type FieldSpec } from '@/platform-ui'
import { connectionsClient, fetchEdiSettings, saveEdiSettings } from './connectionsApi'

const KEY = ['sales', 'config', 'edi-settings']
const errorMessages: Record<string, string> = {
  'edi.connection': 'Choose an active SFTP connection.',
  'auth.forbidden': 'You do not have permission to change the EDI settings.',
}
const schema = z.object({
  connection_id: z.string().nullable().optional(),
  ack_timeout_minutes: z.number({ message: 'Required' }).int().min(1, 'At least 1 minute').max(10080),
  invoice_connection_id: z.string().nullable().optional(),
})

/** P4 / P6: the warehouse SFTP connection for 940s / 943s and 997s, the 997
 *  wait (FR-OB-23) and the 810 connection — shown here, edited in the
 *  standard sheet (sales.config; audited). */
export function EdiSettingsPanel() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const settings = useQuery({ queryKey: KEY, queryFn: fetchEdiSettings })
  const conns = useQuery({
    queryKey: [...KEY, 'sftp-connections'],
    queryFn: () => connectionsClient.list({ page: 1, pageSize: 100, filters: [{ field: 'kind', op: 'eq', value: 'SFTP' }, { field: 'active', op: 'eq', value: 'true' }], sort: [{ field: 'name' }] }),
  })
  if (settings.isLoading || conns.isLoading) return <Skeleton className="h-10 w-full" />
  if (settings.error || conns.error || !settings.data || !conns.data) return <ErrorMessage error={settings.error ?? conns.error} />
  const s = settings.data
  const options = conns.data.items.map((c) => ({ value: String(c.id), label: `${c.name} (${c.mode})` }))
  const name = (id: number | null | undefined) => (id == null ? 'None' : options.find((o) => o.value === String(id))?.label ?? `#${id}`)
  const fields: FieldSpec[] = [
    { name: 'connection_id', label: 'Warehouse connection', type: 'select', options,
      hint: '940s and 943s go to its outbound folder (TEST mode: its test outbound folder); 997s are read from it. None: none is made.' },
    { name: 'ack_timeout_minutes', label: '997 wait (minutes)', type: 'number', required: true, hint: 'No 997 in this time: Timeout and a task.' },
    { name: 'invoice_connection_id', label: '810 invoice connection', type: 'select', options,
      hint: '810 JSON invoices are put in its outbound folder. None: no 810 is sent.' },
  ]
  return (
    <div aria-label="Warehouse EDI" role="group" className="flex flex-wrap items-start justify-between gap-3 text-sm">
      <Section title="Warehouse EDI (940 / 943 / 997) and 810 invoices" plain level={2} label="EDI settings">
        <DetailList items={[['Warehouse connection', name(s.connection_id)], ['997 wait', `${s.ack_timeout_minutes} minutes`],
          ['810 invoice connection', name(s.invoice_connection_id)]]} />
      </Section>
      <ActionButton size="sm" variant="secondary" permission="sales.config" onAction={() => setEditing(true)}>Edit</ActionButton>
      <FormSheet open={editing} onOpenChange={setEditing} title="Warehouse EDI and 810 invoices">
        {editing && (
          <SchemaForm schema={schema} fields={fields} errorMessages={errorMessages} onCancel={() => setEditing(false)}
            defaultValues={{ connection_id: s.connection_id == null ? '' : String(s.connection_id), ack_timeout_minutes: s.ack_timeout_minutes,
              invoice_connection_id: s.invoice_connection_id == null ? '' : String(s.invoice_connection_id) }}
            onSubmit={async (v) => {
              const id = (x: unknown) => (x == null || x === '' ? null : Number(x))
              qc.setQueryData(KEY, await saveEdiSettings({ connection_id: id(v.connection_id), ack_timeout_minutes: Number(v.ack_timeout_minutes),
                invoice_connection_id: id(v.invoice_connection_id) }))
              setEditing(false)
            }} />
        )}
      </FormSheet>
    </div>
  )
}
