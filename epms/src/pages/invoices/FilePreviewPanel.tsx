import { useEffect, useState } from 'react'
import { ExternalLink, FileText } from 'lucide-react'

// Live preview of the uploaded invoice file so the user can verify AI-parsed
// fields against the source document. PDFs render in the browser's native
// viewer (iframe — has its own paging/zoom/search toolbar); images in <img>.
// Owns the blob-URL lifecycle: revoked on file change and unmount.
export function FilePreviewPanel({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let objectUrl: string | null = null
    try { objectUrl = URL.createObjectURL(file) } catch { objectUrl = null }
    setUrl(objectUrl)
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [file])

  const kind = file.type === 'application/pdf'
    ? 'pdf'
    : file.type.startsWith('image/') ? 'image' : 'unsupported'

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-200 bg-neutral-800">
      {/* Toolbar: file name + open in new tab */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-neutral-700 bg-neutral-900 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
          <span className="truncate text-xs font-medium text-neutral-200">{file.name}</span>
        </div>
        {url && (
          <a
            href={url} target="_blank" rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 text-xs text-neutral-400 hover:text-white"
          >
            <ExternalLink className="h-3 w-3" />
            Open in new tab
          </a>
        )}
      </div>
      {url && kind === 'pdf' && (
        <iframe src={url} title="Invoice preview" className="w-full flex-1 border-0" />
      )}
      {url && kind === 'image' && (
        <div className="flex-1 overflow-auto p-3">
          <img src={url} alt="Invoice preview" className="mx-auto max-w-full object-contain" />
        </div>
      )}
      {(!url || kind === 'unsupported') && (
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          Preview not available
        </div>
      )}
    </div>
  )
}
