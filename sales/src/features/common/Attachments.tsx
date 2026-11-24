import { useState } from 'react'
import { Paperclip, Trash2 } from 'lucide-react'
import { z } from 'zod'
import {
  Button, ConfirmAction, IconButton, LocalTable, Section, columnsOf, formatNumber, useFileViewer, useListMeta, useNotices, type ColumnDef, type ListCapabilities, type ListMeta, useLookup, FormDialog, SchemaForm, type FieldSpec, Text, Icon,
} from '@/platform-ui'
import {
  attachFile, downloadRecordFile, listAttachments, recordFile, removeAttachment, type RecordDocument, type RecordType,
} from './recordFiles'

const col = columnsOf<RecordDocument>()
type Offer = NonNullable<ListCapabilities['attachments']>
const megabytes = (o: Offer) => Math.floor(o.max_bytes / 1024 / 1024)
const errorMessages: Record<string, string> = {
  'attachment.empty': 'The file is empty.',
  'attachment.already': 'This file is already attached here.',
  'auth.forbidden': 'You may not attach files here.',
}
const size = (n: number | null | undefined) => (n == null ? '' : n < 1024 * 1024 ? `${formatNumber(Math.ceil(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

const attachSchema = z.object({
  file: z.instanceof(File, { message: 'Choose a file' }),
  note: z.string().trim().max(500).nullable().optional(),
})
// What may be attached is the server's word (/meta capabilities).
const attachFields = (o: Offer): FieldSpec[] => [
  { name: 'file', label: 'File to attach', type: 'file', required: true, accept: o.extensions.map((e) => `.${e}`).join(','), maxBytes: o.max_bytes },
  { name: 'note', label: 'Note', type: 'textarea', placeholder: 'Optional: what it is' },
]

function AttachDialog({ type, id, offer, open, onOpenChange }: { type: RecordType; id: number; offer: Offer; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Attach a file"
      description={`A delivery note, a signed BOL, a photo, an email… up to ${megabytes(offer)} MB. It stays with this record; everyone who can see the record can open it.`}>
      <SchemaForm schema={attachSchema} fields={attachFields(offer)} defaultValues={{ file: null, note: '' }} submitLabel="Attach"
        errorMessages={{ ...errorMessages, 'attachment.too_large': `The file is too large: at most ${megabytes(offer)} MB.`,
          'attachment.type': `This kind of file cannot be attached: ${offer.extensions.join(', ')}.` }} onCancel={() => onOpenChange(false)}
        done="Attached" invalidates={[['attachments', type, id]]} after={() => onOpenChange(false)}
        onSubmit={(v) => attachFile(type, id, v.file as File, (v.note as string | null)?.trim() || null)} />
    </FormDialog>
  )
}

/** A person's files on an order or a transfer (user 2026-11-13): attach, look at, download, remove with a reason
 *  (it is taken off the record; the file itself is kept). Reading needs only sight of the record; attaching and
 *  removing need the record's own change permission. */
export function Attachments({ resource, type, id }: { resource: { basePath: string; meta: () => Promise<ListMeta> }; type: RecordType; id: number }) {
  const offer = useListMeta(resource).data?.capabilities?.attachments
  const can = !!offer?.allowed
  const notices = useNotices()
  const { view, viewer } = useFileViewer()
  const [attaching, setAttaching] = useState(false)
  const [removing, setRemoving] = useState<RecordDocument | null>(null)
  const q = useLookup(['attachments', type, id], () => listAttachments(type, id))
  const rows = q.data ?? []
  const failed = (e: unknown) => notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })
  const columns: ColumnDef<RecordDocument, unknown>[] = [
    col.file('remote_name', 'File', { label: (d) => `Download ${d.remote_name ?? 'file'}`, onDownload: (d) => { downloadRecordFile(d).catch(failed) },
      onView: (d) => view(recordFile(d)) }),
    { accessorKey: 'note', header: 'Note', cell: ({ row }) => <Text size="xs" wrap="words">{row.original.note ?? ''}</Text> },
    { accessorKey: 'size', header: 'Size', cell: ({ row }) => size(row.original.size) },
    { accessorKey: 'created_by_name', header: 'By', cell: ({ row }) => row.original.created_by_name ?? '' },
    col.datetime('created_at', 'Attached'),
    ...(can ? [{ id: 'remove', header: '', cell: ({ row }: { row: { original: RecordDocument } }) => (
      <IconButton label={`Remove ${row.original.remote_name ?? 'file'}`} icon={<Icon icon={Trash2} />} onClick={() => setRemoving(row.original)} />
    ) } as ColumnDef<RecordDocument, unknown>] : []),
  ]
  return (
    <Section title="Attachments" actions={can ? (
      <Button size="sm" variant="secondary" onClick={() => setAttaching(true)}><Icon icon={Paperclip} className="mr-1" />Attach file…</Button>
    ) : undefined}>
      {rows.length
        ? <LocalTable<RecordDocument> ariaLabel="Attachments" columns={columns} rows={rows} rowKey={(d) => d.id} />
        : <Text as="p" tone="muted" size="sm">{q.isLoading ? 'Loading…' : 'No file attached.'}</Text>}
      {viewer}
      {offer && <AttachDialog type={type} id={id} offer={offer} open={attaching} onOpenChange={setAttaching} />}
      {removing && (
        <ConfirmAction open onOpenChange={(o) => { if (!o) setRemoving(null) }} destructive title={`Remove ${removing.remote_name ?? 'this file'}?`}
          description="It is taken off this record (kept in the log with why); the file itself is not deleted."
          confirmLabel="Remove" reason={{ label: 'Why' }} errorMessages={errorMessages}
          onConfirm={async (why) => { await removeAttachment(type, id, removing.id, why); setRemoving(null) }} />
      )}
    </Section>
  )
}
