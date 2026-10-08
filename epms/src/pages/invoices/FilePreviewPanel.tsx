import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink, FileText, Loader2 } from 'lucide-react'
import { pdfjsLib, type PDFDocumentProxy, type RenderTask } from '@/lib/pdfjs'

// Live preview of the uploaded invoice file so the user can verify AI-parsed
// fields against the source document. PDFs are rendered to a canvas with
// pdf.js (same pattern as OA PaDirectCreatePage) — the browser's native PDF
// viewer is NOT used because corporate policy (AlwaysOpenPdfExternally /
// "Download PDFs") blocks inline PDFs in iframes. Images render in <img>.
// Owns the blob-URL lifecycle: revoked on file change and unmount.

function PdfCanvas({ file }: { file: File }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [pageNum, setPageNum] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const pdfRef = useRef<PDFDocumentProxy | null>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)

  useEffect(() => {
    let cancelled = false

    file.arrayBuffer().then(async (buf) => {
      if (cancelled) return
      const pdf = await pdfjsLib.getDocument({ data: buf }).promise
      if (cancelled) { pdf.destroy(); return }
      pdfRef.current = pdf
      setTotalPages(pdf.numPages)
      setPageNum(1)
      setError(false)
      setLoading(false)
    }).catch(() => {
      if (!cancelled) { setError(true); setLoading(false) }
    })

    return () => {
      cancelled = true
      renderTaskRef.current?.cancel()
      pdfRef.current?.destroy()
      pdfRef.current = null
    }
  }, [file])

  useEffect(() => {
    if (!pdfRef.current || loading) return
    const canvas = canvasRef.current
    if (!canvas) return

    let cancelled = false
    renderTaskRef.current?.cancel()

    pdfRef.current.getPage(pageNum).then((page) => {
      if (cancelled) return
      const viewport = page.getViewport({ scale: 2.0 })
      canvas.width = viewport.width
      canvas.height = viewport.height
      const ctx = canvas.getContext('2d')!
      const task = page.render({ canvas, canvasContext: ctx, viewport })
      renderTaskRef.current = task
      return task.promise
    }).catch(() => { /* render cancelled — superseded by a newer render */ })

    return () => { cancelled = true; renderTaskRef.current?.cancel() }
  }, [pageNum, loading])

  if (loading) return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-neutral-400">
      <Loader2 className="h-4 w-4 animate-spin" /> Rendering PDF…
    </div>
  )
  if (error) return (
    <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
      Preview not available
    </div>
  )

  return (
    <>
      <div className="flex-1 overflow-auto p-3">
        <canvas ref={canvasRef} className="mx-auto max-w-full rounded shadow-sm" />
      </div>
      {totalPages > 1 && (
        <div className="flex shrink-0 items-center justify-center gap-3 border-t border-neutral-700 bg-neutral-900 py-1.5 text-xs text-neutral-300">
          <button
            type="button"
            onClick={() => setPageNum((p) => Math.max(1, p - 1))}
            disabled={pageNum === 1}
            className="rounded p-1 hover:bg-neutral-700 disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span>Page {pageNum} / {totalPages}</span>
          <button
            type="button"
            onClick={() => setPageNum((p) => Math.min(totalPages, p + 1))}
            disabled={pageNum === totalPages}
            className="rounded p-1 hover:bg-neutral-700 disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </>
  )
}

export function FilePreviewPanel({ file }: { file: File }) {
  const url = useMemo(() => {
    try { return URL.createObjectURL(file) } catch { return null }
  }, [file])

  useEffect(() => {
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [url])

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
      {kind === 'pdf' && <PdfCanvas file={file} />}
      {url && kind === 'image' && (
        <div className="flex-1 overflow-auto p-3">
          <img src={url} alt="Invoice preview" className="mx-auto max-w-full object-contain" />
        </div>
      )}
      {(!url || kind === 'unsupported') && kind !== 'pdf' && (
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          Preview not available
        </div>
      )}
    </div>
  )
}
