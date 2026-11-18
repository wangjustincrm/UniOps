/**
 * ChainAttachmentsPanel — right-side slide-over listing every attachment across
 * a PA's document chain (PR/PO/GR/INV/PA), with single + bulk download/print.
 */
import { useState } from 'react'
import { Button, Drawer, IconButton } from '@uniops/shell'
import {
  Download, Printer, FileText, Package, Warehouse, Receipt, CreditCard, Loader2, ImageIcon,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth.store'
import { useChainAttachments, type ChainDocGroup, type ChainAttachment } from '@/hooks/useChainAttachments'
import {
  fetchAuthedBlob, saveBlob, downloadAllAsZip, printFiles, type BundleFile,
} from '@/lib/attachmentBundle'
import { formatBytes } from '@/lib/utils'
import { ActionMessage } from '@/components/ui/ConfirmDialog'

const DOC_ICON: Record<ChainDocGroup['docType'], React.ReactNode> = {
  PR: <FileText className="h-3.5 w-3.5" />,
  PO: <Package className="h-3.5 w-3.5" />,
  GR: <Warehouse className="h-3.5 w-3.5" />,
  RECEIPT: <ImageIcon className="h-3.5 w-3.5" />,
  INV: <Receipt className="h-3.5 w-3.5" />,
  PA: <CreditCard className="h-3.5 w-3.5" />,
}

function toBundle(g: ChainDocGroup, a: ChainAttachment): BundleFile {
  return {
    filename: a.filename,
    folder: `${g.docType}-${g.docNumber}`,
    fetchUrl: a.fetchUrl,
    contentType: a.contentType,
    printable: a.printable,
  }
}

export function ChainAttachmentsPanel({
  paId, paNumber, onClose,
}: {
  paId: string
  paNumber: string
  onClose: () => void
}) {
  const token = useAuthStore((s) => s.token)
  const { groups, total, isLoading, error } = useChainAttachments(paId)
  const [busy, setBusy] = useState<string | null>(null)
  const [rowBusy, setRowBusy] = useState<string | null>(null)
  const isBusy = busy !== null || rowBusy !== null
  // What a download / print did, shown under the toolbar (were browser alerts).
  const [message, setMessage] = useState<{ tone: 'error' | 'warning'; text: string } | null>(null)
  const fail = (text: string) => setMessage({ tone: 'error', text })
  const partly = (text: string) => setMessage({ tone: 'warning', text })

  const allFiles: BundleFile[] = groups.flatMap((g) => g.attachments.map((a) => toBundle(g, a)))

  const handleSingleDownload = async (a: ChainAttachment) => {
    setRowBusy(a.id)
    setMessage(null)
    try {
      const blob = await fetchAuthedBlob(a.fetchUrl, token)
      saveBlob(blob, a.filename)
    } catch { fail(`Download failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleSinglePrint = async (g: ChainDocGroup, a: ChainAttachment) => {
    setRowBusy(a.id)
    setMessage(null)
    try {
      const r = await printFiles([toBundle(g, a)], token)
      if (r.ok === 0) fail('Nothing printable in this file.')
    } catch { fail(`Print failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleDownloadAll = async () => {
    setBusy('Zipping…')
    setMessage(null)
    try {
      const r = await downloadAllAsZip(
        allFiles, token, `PA-${paNumber}-attachments.zip`,
        (done, t) => setBusy(`Zipping ${done}/${t}…`),
      )
      if (r.skipped) partly(`Downloaded ${r.ok} file(s). Skipped ${r.skipped} that failed.`)
    } catch { fail('Download-all failed.') }
    finally { setBusy(null) }
  }

  const handlePrintAll = async () => {
    setBusy('Preparing print…')
    setMessage(null)
    try {
      const r = await printFiles(
        allFiles, token,
        (done, t) => setBusy(`Preparing print ${done}/${t}…`),
      )
      if (r.ok === 0) fail('No printable files in this chain.')
      else if (r.skipped) partly(`Printing ${r.ok} file(s). Skipped ${r.skipped} non-printable.`)
    } catch { fail('Print-all failed.') }
    finally { setBusy(null) }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      busy={isBusy}
      width="sm"
      title={<>Chain Attachments{total > 0 && ` (${total})`}</>}
      bodyClassName="flex flex-col p-0"
    >
        <div className="flex items-center gap-2 border-b border-line-subtle bg-surface-subtle px-4 py-2">
          <Button variant="secondary" size="xs" onClick={handleDownloadAll} disabled={total === 0 || isBusy}>
            <Download className="h-3.5 w-3.5" /> Download all (ZIP)
          </Button>
          <Button variant="secondary" size="xs" onClick={handlePrintAll} disabled={total === 0 || isBusy}>
            <Printer className="h-3.5 w-3.5" /> Print all
          </Button>
          {busy && (
            <span className="ml-auto inline-flex items-center gap-1 text-xs text-fg-muted">
              <Loader2 className="h-3 w-3 animate-spin" />{busy}
            </span>
          )}
        </div>

        {message && (
          <ActionMessage tone={message.tone} onDismiss={() => setMessage(null)} className="mx-4 mt-3 text-xs">
            {message.text}
          </ActionMessage>
        )}

        <div className="flex-1 overflow-auto px-4 py-3">
          {isLoading ? (
            <p className="flex items-center gap-2 text-xs text-fg-muted">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading attachments…
            </p>
          ) : total === 0 ? (
            error ? (
              <p className="text-xs text-danger-fg">Some attachments could not be loaded.</p>
            ) : (
              <p className="text-xs text-fg-muted">No attachments found in this document chain.</p>
            )
          ) : (
            <div className="space-y-4">
              {error && (
                <p className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-2 text-2xs text-danger-fg">
                  Some attachments could not be loaded — showing what's available.
                </p>
              )}
              {groups.filter((g) => g.attachments.length > 0).map((g) => (
                <div key={`${g.docType}-${g.docId}`}>
                  <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-fg-secondary">
                    <span className="text-fg-muted">{DOC_ICON[g.docType]}</span>
                    {g.docNumber}
                    <span className="font-normal text-fg-muted">· {g.attachments.length} file{g.attachments.length > 1 ? 's' : ''}</span>
                  </div>
                  <ul className="space-y-1">
                    {g.attachments.map((a) => (
                      <li key={a.id}
                        className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-2 text-xs">
                        <FileText className="h-4 w-4 shrink-0 text-fg-muted" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-fg-secondary">{a.filename}</p>
                          <p className="text-2xs text-fg-muted">{formatBytes(a.sizeBytes)}</p>
                        </div>
                        {rowBusy === a.id && <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-muted" />}
                        <IconButton
                          label="Print"
                          title={a.printable ? 'Print' : 'Preview/print not supported — download instead'}
                          icon={<Printer className="h-3.5 w-3.5" />}
                          size="icon-xs"
                          onClick={() => handleSinglePrint(g, a)}
                          disabled={!a.printable || isBusy}
                        />
                        <IconButton
                          label="Download"
                          icon={<Download className="h-3.5 w-3.5" />}
                          size="icon-xs"
                          variant="ghost"
                          onClick={() => handleSingleDownload(a)}
                          disabled={isBusy}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
    </Drawer>
  )
}
