/**
 * ChainAttachmentsPanel — right-side slide-over listing every attachment across
 * a PA's document chain (PR/PO/GR/INV/PA), with single + bulk download/print.
 */
import { useState } from 'react'
import {
  X, Download, Printer, FileText, Package, Warehouse, Receipt, CreditCard, Loader2, ImageIcon,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth.store'
import { useChainAttachments, type ChainDocGroup, type ChainAttachment } from '@/hooks/useChainAttachments'
import {
  fetchAuthedBlob, saveBlob, downloadAllAsZip, printFiles, type BundleFile,
} from '@/lib/attachmentBundle'
import { formatBytes } from '@/lib/utils'

const DOC_ICON: Record<ChainDocGroup['docType'], React.ReactNode> = {
  PR: <FileText className="h-3.5 w-3.5" />,
  PO: <Package className="h-3.5 w-3.5" />,
  GR: <Warehouse className="h-3.5 w-3.5" />,
  SLIP: <ImageIcon className="h-3.5 w-3.5" />,
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

  const allFiles: BundleFile[] = groups.flatMap((g) => g.attachments.map((a) => toBundle(g, a)))

  const handleSingleDownload = async (a: ChainAttachment) => {
    setRowBusy(a.id)
    try {
      const blob = await fetchAuthedBlob(a.fetchUrl, token)
      saveBlob(blob, a.filename)
    } catch { alert(`Download failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleSinglePrint = async (g: ChainDocGroup, a: ChainAttachment) => {
    setRowBusy(a.id)
    try {
      const r = await printFiles([toBundle(g, a)], token)
      if (r.ok === 0) alert('Nothing printable in this file.')
    } catch { alert(`Print failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleDownloadAll = async () => {
    setBusy('Zipping…')
    try {
      const r = await downloadAllAsZip(
        allFiles, token, `PA-${paNumber}-attachments.zip`,
        (done, t) => setBusy(`Zipping ${done}/${t}…`),
      )
      if (r.skipped) alert(`Downloaded ${r.ok} file(s). Skipped ${r.skipped} that failed.`)
    } catch { alert('Download-all failed.') }
    finally { setBusy(null) }
  }

  const handlePrintAll = async () => {
    setBusy('Preparing print…')
    try {
      const r = await printFiles(
        allFiles, token,
        (done, t) => setBusy(`Preparing print ${done}/${t}…`),
      )
      if (r.ok === 0) alert('No printable files in this chain.')
      else if (r.skipped) alert(`Printing ${r.ok} file(s). Skipped ${r.skipped} non-printable.`)
    } catch { alert('Print-all failed.') }
    finally { setBusy(null) }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={isBusy ? undefined : onClose} />
      <div className="relative flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-700">
            Chain Attachments{total > 0 && ` (${total})`}
          </h2>
          <button onClick={onClose} disabled={isBusy}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-100 disabled:opacity-40">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-neutral-100 bg-neutral-50 px-4 py-2">
          <button onClick={handleDownloadAll} disabled={total === 0 || isBusy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-40">
            <Download className="h-3.5 w-3.5" /> Download all (ZIP)
          </button>
          <button onClick={handlePrintAll} disabled={total === 0 || isBusy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-40">
            <Printer className="h-3.5 w-3.5" /> Print all
          </button>
          {busy && (
            <span className="ml-auto inline-flex items-center gap-1 text-xs text-neutral-500">
              <Loader2 className="h-3 w-3 animate-spin" />{busy}
            </span>
          )}
        </div>

        <div className="flex-1 overflow-auto px-4 py-3">
          {isLoading ? (
            <p className="flex items-center gap-2 text-xs text-neutral-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading attachments…
            </p>
          ) : total === 0 ? (
            error ? (
              <p className="text-xs text-danger-600">Some attachments could not be loaded.</p>
            ) : (
              <p className="text-xs text-neutral-400 italic">No attachments found in this document chain.</p>
            )
          ) : (
            <div className="space-y-4">
              {error && (
                <p className="rounded-lg border border-danger-200 bg-danger-50 px-2.5 py-2 text-[11px] text-danger-600">
                  Some attachments could not be loaded — showing what's available.
                </p>
              )}
              {groups.filter((g) => g.attachments.length > 0).map((g) => (
                <div key={`${g.docType}-${g.docId}`}>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
                    <span className="text-neutral-400">{DOC_ICON[g.docType]}</span>
                    {g.docNumber}
                    <span className="font-normal text-neutral-400">· {g.attachments.length} file{g.attachments.length > 1 ? 's' : ''}</span>
                  </div>
                  <ul className="space-y-1">
                    {g.attachments.map((a) => (
                      <li key={a.id}
                        className="flex items-center gap-2 rounded-lg border border-neutral-200 px-2.5 py-2 text-xs">
                        <FileText className="h-4 w-4 shrink-0 text-neutral-400" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-neutral-700">{a.filename}</p>
                          <p className="text-[10px] text-neutral-400">{formatBytes(a.sizeBytes)}</p>
                        </div>
                        {rowBusy === a.id && <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-400" />}
                        <button
                          title={a.printable ? 'Print' : 'Preview/print not supported — download instead'}
                          onClick={() => handleSinglePrint(g, a)}
                          disabled={!a.printable || isBusy}
                          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30">
                          <Printer className="h-3.5 w-3.5" />
                        </button>
                        <button title="Download" onClick={() => handleSingleDownload(a)}
                          disabled={isBusy}
                          className="rounded p-1 text-primary-600 hover:bg-primary-50 disabled:opacity-30">
                          <Download className="h-3.5 w-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
