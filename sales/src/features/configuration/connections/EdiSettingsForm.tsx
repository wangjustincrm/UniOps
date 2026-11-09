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
  finance_email: z.union([z.literal(''), z.string().trim().email('Enter an email address')]).nullable().optional(),
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
    { name: 'finance_email', label: 'Finance AR email', type: 'text',
      hint: 'Gets the PO, the invoice and the packing list of every invoice sent: copied on an emailed invoice, its own message for an 810. Empty: nobody.' },
  ]
  return (
    <SchemaForm schema={schema} fields={fields} errorMessages={errorMessages} onCancel={onDone} readOnly={!canEdit}
      defaultValues={{ connection_id: s.connection_id == null ? '' : String(s.connection_id), ack_timeout_minutes: s.ack_timeout_minutes,
        invoice_connection_id: s.invoice_connection_id == null ? '' : String(s.invoice_connection_id), finance_email: s.finance_email ?? '' }}
      onSubmit={async (v) => {
        const id = (x: unknown) => (x == null || x === '' ? null : Number(x))
        qc.setQueryData(EDI_KEY, await saveEdiSettings({ connection_id: id(v.connection_id), ack_timeout_minutes: Number(v.ack_timeout_minutes),
          invoice_connection_id: id(v.invoice_connection_id), finance_email: (v.finance_email as string | null) || null }))
        onDone()
      }} />
  )
}
