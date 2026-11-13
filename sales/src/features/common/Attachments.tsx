import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Paperclip, Trash2 } from 'lucide-react'
import {
  ActionButton, Button, ConfirmAction, Dialog, FilePicker, IconButton, LocalTable, Section, Textarea, columnsOf, formatNumber, useCan,
  useFileViewer, useNotices, type ColumnDef,
} from '@/platform-ui'
import {
  ATTACH_PERMISSION, attachFile, downloadRecordFile, listAttachments, recordFile, removeAttachment, type RecordDocument, type RecordType,
} from './recordFiles'

const col = columnsOf<RecordDocument>()
const MAX_MB = 20
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.tif,.tiff,.txt,.csv,.xls,.xlsx,.doc,.docx,.ppt,.pptx,.msg,.eml,.edi,.json,.xml'
const errorMessages: Record<string, string> = {
  'attachment.type': 'This kind of file cannot be attached: PDF, pictures, mail, Office, CSV, text or EDI.',
  'attachment.too_large': `The file is too large: at most ${MAX_MB} MB.`,
  'attachment.empty': 'The file is empty.',
  'attachment.already': 'This file is already attached here.',
  'auth.forbidden': 'You may not attach files here.',
}
const size = (n: number | null | undefined) => (n == null ? '' : n < 1024 * 1024 ? `${formatNumber(Math.ceil(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

function AttachDialog({ type, id, open, onOpenChange }: { type: RecordType; id: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [note, setNote] = useState('')
  const tooLarge = !!file && file.size > MAX_MB * 1024 * 1024
  const close = () => { setFile(null); setNote(''); onOpenChange(false) }
  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())} title="Attach a file"
      description={`A delivery note, a signed BOL, a photo, an email… up to ${MAX_MB} MB. It stays with this record; everyone who can see the record can open it.`}
      footer={<>
        <Button variant="secondary" onClick={close}>Cancel</Button>
        <ActionButton disabled={!file || tooLarge} errorMessages={errorMessages} done="Attached"
          onAction={async () => { await attachFile(type, id, file!, note.trim() || null); close() }}>Attach</ActionButton>
      </>}>
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <FilePicker label="File to attach" accept={ACCEPT} onFile={setFile}>{file ? 'Choose another file' : 'Choose a file'}</FilePicker>
          <span className="min-w-0 break-all text-sm text-neutral-700">{file ? `${file.name} · ${size(file.size)}` : 'No file chosen'}</span>
        </div>
        {tooLarge && <p className="text-sm text-danger-600">Too large: at most {MAX_MB} MB.</p>}
        <Textarea aria-label="Note" rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional): what it is" />
      </div>
    </Dialog>
  )
}

/** A person's files on an order or a transfer (user 2026-11-13): attach, look at, download, remove with a reason
 *  (it is taken off the record; the file itself is kept). Reading needs only sight of the record; attaching and
 *  removing need the record's own change permission. */
export function Attachments({ type, id }: { type: RecordType; id: number }) {
  const can = useCan(ATTACH_PERMISSION[type])
  const notices = useNotices()
  const { view, viewer } = useFileViewer()
  const [attaching, setAttaching] = useState(false)
  const [removing, setRemoving] = useState<RecordDocument | null>(null)
  const q = useQuery({ queryKey: ['attachments', type, id], queryFn: () => listAttachments(type, id) })
  const rows = q.data ?? []
  const failed = (e: unknown) => notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })
  const columns: ColumnDef<RecordDocument, unknown>[] = [
    col.file('remote_name', 'File', { label: (d) => `Download ${d.remote_name ?? 'file'}`, onDownload: (d) => { downloadRecordFile(d).catch(failed) },
      onView: (d) => view(recordFile(d)) }),
    { accessorKey: 'note', header: 'Note', cell: ({ row }) => <span className="break-words text-xs">{row.original.note ?? ''}</span> },
    { accessorKey: 'size', header: 'Size', cell: ({ row }) => size(row.original.size) },
    { accessorKey: 'created_by_name', header: 'By', cell: ({ row }) => row.original.created_by_name ?? '' },
    col.datetime('created_at', 'Attached'),
    ...(can ? [{ id: 'remove', header: '', cell: ({ row }: { row: { original: RecordDocument } }) => (
      <IconButton label={`Remove ${row.original.remote_name ?? 'file'}`} icon={<Trash2 className="h-4 w-4" />} onClick={() => setRemoving(row.original)} />
    ) } as ColumnDef<RecordDocument, unknown>] : []),
  ]
  return (
    <Section title="Attachments" actions={can ? (
      <Button size="sm" variant="secondary" onClick={() => setAttaching(true)}><Paperclip className="mr-1 h-4 w-4" aria-hidden />Attach file…</Button>
    ) : undefined}>
      {rows.length
        ? <LocalTable<RecordDocument> ariaLabel="Attachments" columns={columns} rows={rows} rowKey={(d) => d.id} />
        : <p className="text-sm text-neutral-500">{q.isLoading ? 'Loading…' : 'No file attached.'}</p>}
      {viewer}
      <AttachDialog type={type} id={id} open={attaching} onOpenChange={setAttaching} />
      {removing && (
        <ConfirmAction open onOpenChange={(o) => { if (!o) setRemoving(null) }} destructive title={`Remove ${removing.remote_name ?? 'this file'}?`}
          description="It is taken off this record (kept in the log with why); the file itself is not deleted."
          confirmLabel="Remove" reason={{ label: 'Why' }} errorMessages={errorMessages}
          onConfirm={async (why) => { await removeAttachment(type, id, removing.id, why); setRemoving(null) }} />
      )}
    </Section>
  )
}
