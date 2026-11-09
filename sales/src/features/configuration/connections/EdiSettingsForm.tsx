import { useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { SchemaForm, useCan, type FieldSpec } from '@/platform-ui'
import { saveEdiSettings, type EdiSettings } from './connectionsApi'
import { EDI_KEY } from './connectionSettings'

const errorMessages: Record<string, string> = {
  'edi.connection': 'Choose an active SFTP connection.',
  'auth.forbidden': 'You do not have permission to change the EDI settings.',
}
const schema = z.object({
  connection_id: z.string().nullable().optional(),
  ack_timeout_minutes: z.number({ message: 'Required' }).int().min(1, 'At least 1 minute').max(10080),
  invoice_connection_id: z.string().nullable().optional(),
  internal_emails: z.array(z.string().trim().email('Each one must be an email address')).max(20, 'At most 20 addresses'),
})

/** The warehouse SFTP connection for 940s / 943s and 997s, the 997 wait and
 *  the 810 connection (sales.config to save; audited; a reader sees them). */
export function EdiSettingsForm({ settings: s, options, onDone }: {
  settings: EdiSettings; options: { value: string; label: string }[]; onDone: () => void
}) {
  const qc = useQueryClient()
  const canEdit = useCan('sales.config')
  const fields: FieldSpec[] = [
    { name: 'connection_id', label: 'Warehouse connection', type: 'select', options,
      hint: '940s and 943s go to its outbound folder and 997s are read from it; a Test connection writes only to its test folder. None: none is made.' },
    { name: 'ack_timeout_minutes', label: '997 wait (minutes)', type: 'number', required: true, hint: 'No 997 in this time: Timeout and a task.' },
    { name: 'invoice_connection_id', label: '810 invoice connection', type: 'select', options,
      hint: '810 JSON invoices are put in its outbound folder. None: no 810 is sent.' },
    { name: 'internal_emails', label: 'Internal invoice recipients', type: 'tags',
      hint: 'Company addresses only. Each time an invoice is sent they get every document: the invoice, the PO, the packing list and, for an 810 partner, the 810 file. Sent in Test too; partners get theirs only as their configuration says. Empty: nobody.' },
  ]
  return (
    <SchemaForm schema={schema} fields={fields} errorMessages={errorMessages} onCancel={onDone} readOnly={!canEdit}
      defaultValues={{ connection_id: s.connection_id == null ? '' : String(s.connection_id), ack_timeout_minutes: s.ack_timeout_minutes,
        invoice_connection_id: s.invoice_connection_id == null ? '' : String(s.invoice_connection_id), internal_emails: s.internal_emails ?? [] }}
      onSubmit={async (v) => {
        const id = (x: unknown) => (x == null || x === '' ? null : Number(x))
        qc.setQueryData(EDI_KEY, await saveEdiSettings({ connection_id: id(v.connection_id), ack_timeout_minutes: Number(v.ack_timeout_minutes),
          invoice_connection_id: id(v.invoice_connection_id), internal_emails: (v.internal_emails as string[] | undefined) ?? [] }))
        onDone()
      }} />
  )
}
