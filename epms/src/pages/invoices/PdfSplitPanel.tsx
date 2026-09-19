import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Loader2, Scissors, AlertTriangle, X, ChevronLeft, ChevronRight, Maximize2, Minimize2,
} from 'lucide-react'
import { pdfjsLib, type PDFDocumentProxy, type RenderTask } from '@/lib/pdfjs'
import {
  MAX_SPLITTABLE_PAGES, describeGroup, groupsFromCuts, type PageGroup,
} from '@/lib/pdf-split'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// Where a multi-page PDF is cut into one file per invoice — by a person,
// looking at the pages, before anything is sent for extraction.
//
// Deliberately not automated. A wrong cut merges two invoices into one (the
// second is never paid) or halves one (half of it is never paid), and neither
// shows up anywhere downstream — an invoice for the wrong amount matches,
// approves and pays exactly like a right one. Nothing in a heuristic can prove
// a boundary; a person sees it at a glance. The presets are there so the common
// shapes are one click, not so the machine decides.

/**
 * One page, big enough to actually read.
 *
 * The thumbnails are for seeing the shape of the file — where a header starts,
 * where a remittance stub ends. They are far too small to read an invoice
 * number off, which is the one thing that settles whether page 4 is still the
 * invoice that began on page 3. So the decision is made here as well: the cut
 * for "this page starts a new invoice" is one button away from the page you
 * are looking at, instead of back on a thumbnail you then have to find again.
 *
 * Rendered at scale 2 and shown fitted by default; "Actual size" drops the cap
 * and lets the container scroll, for the small print.
 */
function PagePreview({ pdf, page, pageCount, groupIndex, startsInvoice, onToggleStart, onGo, onClose }: {
  pdf: PDFDocumentProxy
  page: number
  pageCount: number
  groupIndex: number
  startsInvoice: boolean
  onToggleStart: () => void
  onGo: (page: number) => void
  onClose: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const taskRef = useRef<RenderTask | null>(null)
  const [fit, setFit] = useState(true)
  const [rendering, setRendering] = useState(true)

  // The zoom is a RE-RENDER, not a CSS resize.
  //
  // The first attempt fitted with `max-h-full` on the canvas and toggled it
  // off for actual size. It did nothing visible: a percentage max-height only
  // binds against a definite container height, and inside a centred flex row
  // there is none — so the canvas sat at its natural size in both states and
  // the button looked broken. Deriving the scale from the box's measured size
  // has no such dependency, and it renders at the resolution actually shown
  // instead of letting the browser resample a bitmap drawn for another size.
  useEffect(() => {
    let cancelled = false
    setRendering(true)
    taskRef.current?.cancel()

    pdf.getPage(page).then((p) => {
      if (cancelled) return
      const canvas = canvasRef.current
      const box = boxRef.current
      if (!canvas || !box) return

      const unit = p.getViewport({ scale: 1 })
      // Fit: as large as the box allows. Actual size: 2 CSS px per PDF point,
      // ~200%, which is what reading a vendor's small print takes.
      const fitScale = Math.min((box.clientWidth - 24) / unit.width,
                                (box.clientHeight - 24) / unit.height)
      // A box that has not been laid out yet measures 0, and a scale of 0 draws
      // a 0x0 canvas — indistinguishable from "the viewer is broken".
      const scale = fit ? (fitScale > 0 ? fitScale : 1) : 2
      // Drawn at device resolution and laid out at CSS size, so a HiDPI screen
      // gets a sharp page rather than an upscaled one.
      const dpr = window.devicePixelRatio || 1
      const viewport = p.getViewport({ scale: scale * dpr })
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.style.width = `${viewport.width / dpr}px`
      canvas.style.height = `${viewport.height / dpr}px`

      const task = p.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport })
      taskRef.current = task
      return task.promise.then(() => { if (!cancelled) setRendering(false) })
    }).catch(() => { /* superseded by a newer render */ })

    return () => { cancelled = true; taskRef.current?.cancel() }
  }, [pdf, page, fit])

  // Arrow keys page through, Escape closes — the same reflexes any viewer has.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft' && page > 1) onGo(page - 1)
      if (e.key === 'ArrowRight' && page < pageCount) onGo(page + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [page, pageCount, onGo, onClose])

  return createPortal(
    // z-60: this opens from inside the upload modal, which is z-50.
    // Absolute bands rather than a flex column: the viewport box needs a
    // definite height for both the scroll and the fit measurement above.
    <div className="fixed inset-0 z-[60] bg-neutral-900/80 backdrop-blur-sm" onClick={onClose}>
      <div className="absolute inset-x-0 top-0 flex h-14 items-center justify-between gap-3 px-5 text-sm text-neutral-100"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3">
          <span className="font-medium">Page {page} of {pageCount}</span>
          <span className="rounded bg-neutral-700 px-2 py-0.5 text-xs">Invoice #{groupIndex + 1}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onToggleStart}
            disabled={page === 1}
            title={page === 1 ? 'The first page always starts the first invoice' : undefined}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
              page === 1 ? 'cursor-not-allowed bg-neutral-700 text-neutral-500'
                : startsInvoice ? 'bg-primary-600 text-white hover:bg-primary-700'
                : 'bg-neutral-700 text-neutral-100 hover:bg-neutral-600',
            )}
          >
            <Scissors className="h-3.5 w-3.5" />
            {startsInvoice ? 'Starts a new invoice' : 'Start a new invoice here'}
          </button>
          <button type="button" onClick={() => setFit((f) => !f)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-neutral-700 px-3 py-1.5 text-xs font-medium text-neutral-100 hover:bg-neutral-600">
            {fit ? <><Maximize2 className="h-3.5 w-3.5" /> Actual size</>
                 : <><Minimize2 className="h-3.5 w-3.5" /> Fit to window</>}
          </button>
          <button type="button" onClick={onClose}
                  className="rounded-lg bg-neutral-700 p-1.5 text-neutral-100 hover:bg-neutral-600">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div ref={boxRef}
           className="absolute inset-x-14 bottom-4 top-14 overflow-auto rounded-lg bg-neutral-800 p-3"
           onClick={(e) => e.stopPropagation()}>
        {rendering && (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-neutral-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Rendering…
          </div>
        )}
        {/* mx-auto, not a centring flex parent: a centred flex item that
            overflows its scroll container has its top-left cut off and
            unreachable, which is exactly the state "actual size" produces. */}
        <canvas ref={canvasRef}
                className={cn('mx-auto block rounded bg-white shadow-lg', rendering && 'hidden')} />
      </div>

      <button type="button" disabled={page === 1}
              onClick={(e) => { e.stopPropagation(); onGo(page - 1) }}
              className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-neutral-700/90 p-2 text-white hover:bg-neutral-600 disabled:opacity-20">
        <ChevronLeft className="h-5 w-5" />
      </button>
      <button type="button" disabled={page === pageCount}
              onClick={(e) => { e.stopPropagation(); onGo(page + 1) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-neutral-700/90 p-2 text-white hover:bg-neutral-600 disabled:opacity-20">
        <ChevronRight className="h-5 w-5" />
      </button>
    </div>,
    document.body,
  )
}

interface Props {
  file: File
  onConfirm: (groups: PageGroup[]) => void
  onCancel: () => void
  busy?: boolean
}

export function PdfSplitPanel({ file, onConfirm, onCancel, busy = false }: Props) {
  const [pageCount, setPageCount] = useState(0)
  const [thumbs, setThumbs] = useState<string[]>([])
  const [failed, setFailed] = useState(false)
  // A cut sits AFTER the page number it holds, so page n and n+1 end up in
  // different invoices. Empty = the whole file is one invoice, which is what
  // uploading has always done and so is the only safe default here.
  const [cutsAfter, setCutsAfter] = useState<Set<number>>(new Set())
  // The document stays open after the thumbnails are drawn: the big preview
  // re-renders a page at full scale on demand, and re-opening the file for
  // every click would cost a second each time on a 1.3 MB PDF.
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [previewPage, setPreviewPage] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setThumbs([]); setFailed(false); setPageCount(0); setCutsAfter(new Set())
    setPreviewPage(null); setPdf(null)

    ;(async () => {
      const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise
      if (cancelled) { pdf.destroy(); return }
      setPageCount(pdf.numPages)
      setPdf(pdf)

      // Rendered one at a time and published as they arrive: a 20-page file
      // fills in rather than showing nothing for several seconds.
      const rendered: string[] = []
      for (let i = 1; i <= Math.min(pdf.numPages, MAX_SPLITTABLE_PAGES); i++) {
        if (cancelled) break
        const page = await pdf.getPage(i)
        const viewport = page.getViewport({ scale: 0.45 })
        const canvas = document.createElement('canvas')
        canvas.width = viewport.width
        canvas.height = viewport.height
        await page.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise
        rendered.push(canvas.toDataURL('image/jpeg', 0.7))
        if (!cancelled) setThumbs([...rendered])
      }
      // NOT destroyed here. Ownership passes at setPdf: before it, the early
      // return above releases the document; after it, the effect below does.
      // Destroying in both places double-destroys a cancelled mid-render load.
    })().catch(() => { if (!cancelled) setFailed(true) })

    return () => { cancelled = true }
  }, [file])

  // The proxy outlives the render loop, so releasing it is its own concern:
  // on unmount, and whenever a different document replaces it.
  useEffect(() => () => { pdf?.destroy() }, [pdf])

  const groups = useMemo(
    () => (pageCount ? groupsFromCuts(pageCount, cutsAfter) : []),
    [pageCount, cutsAfter],
  )

  const toggleCut = (afterPage: number) => setCutsAfter((prev) => {
    const next = new Set(prev)
    if (next.has(afterPage)) next.delete(afterPage); else next.add(afterPage)
    return next
  })

  /** Cut every `size` pages — "every page is an invoice" is size 1. */
  const applyEvery = (size: number) => {
    const next = new Set<number>()
    for (let p = size; p < pageCount; p += size) next.add(p)
    setCutsAfter(next)
  }

  const tooManyPages = pageCount > MAX_SPLITTABLE_PAGES

  if (failed) return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-neutral-500">
      <AlertTriangle className="h-4 w-4" />
      This PDF could not be read for splitting — continue and it uploads as one invoice.
      <Button size="sm" variant="secondary" onClick={onCancel}>Continue</Button>
    </div>
  )

  if (!pageCount) return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-neutral-400">
      <Loader2 className="h-4 w-4 animate-spin" /> Reading pages…
    </div>
  )

  return (
    <div className="flex flex-1 min-h-0 flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold text-neutral-900">
          This PDF has {pageCount} pages — how many invoices is it?
        </h3>
        <p className="mt-1 text-xs text-neutral-500">
          Click a page to see it full size. Click the scissors between two pages
          to start a new invoice there. Each invoice is then read, checked and
          created on its own, with its own pages attached.
        </p>
      </div>

      {tooManyPages ? (
        <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {pageCount} pages is more than this can show at once (limit {MAX_SPLITTABLE_PAGES}).
            Split the file outside EPMS first, or upload it as a single invoice.
          </span>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-neutral-500">Quick set:</span>
            <Button size="sm" variant="secondary" onClick={() => applyEvery(1)}>Every page</Button>
            <Button size="sm" variant="secondary" onClick={() => applyEvery(2)}>Every 2 pages</Button>
            <Button size="sm" variant="secondary" onClick={() => setCutsAfter(new Set())}>
              One invoice
            </Button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto rounded-xl border border-neutral-200 bg-neutral-50 p-3">
            <div className="flex flex-wrap items-stretch gap-y-4">
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((page) => {
                const groupIndex = groups.findIndex((g) => g.includes(page))
                const isCut = cutsAfter.has(page)
                return (
                  <div key={page} className="flex items-stretch">
                    <div className="flex w-[104px] flex-col items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setPreviewPage(page)}
                        title={`Open page ${page} full size`}
                        className={cn(
                          'group relative flex h-[134px] w-[100px] items-center justify-center overflow-hidden rounded border bg-white',
                          'transition hover:ring-2 hover:ring-primary-500',
                          groupIndex % 2 === 0 ? 'border-primary-200' : 'border-neutral-300',
                        )}
                      >
                        {thumbs[page - 1]
                          ? <img src={thumbs[page - 1]} alt={`Page ${page}`} className="max-h-full max-w-full" />
                          : <Loader2 className="h-4 w-4 animate-spin text-neutral-300" />}
                        <span className="pointer-events-none absolute inset-0 hidden items-center justify-center bg-neutral-900/50 group-hover:flex">
                          <Maximize2 className="h-4 w-4 text-white" />
                        </span>
                      </button>
                      <span className="text-[11px] text-neutral-500">
                        p{page} · <span className="font-medium text-neutral-700">#{groupIndex + 1}</span>
                      </span>
                    </div>
                    {page < pageCount && (
                      <button
                        type="button"
                        onClick={() => toggleCut(page)}
                        title={isCut ? `Join page ${page} and ${page + 1}` : `Start a new invoice at page ${page + 1}`}
                        className={cn(
                          'mx-0.5 flex w-6 shrink-0 items-center justify-center rounded transition-colors',
                          isCut
                            ? 'bg-primary-600 text-white hover:bg-primary-700'
                            : 'text-neutral-300 hover:bg-neutral-200 hover:text-neutral-600',
                        )}
                      >
                        <Scissors className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </>
      )}

      {pdf && previewPage !== null && (
        <PagePreview
          pdf={pdf}
          page={previewPage}
          pageCount={pageCount}
          groupIndex={groups.findIndex((g) => g.includes(previewPage))}
          startsInvoice={cutsAfter.has(previewPage - 1)}
          onToggleStart={() => toggleCut(previewPage - 1)}
          onGo={setPreviewPage}
          onClose={() => setPreviewPage(null)}
        />
      )}

      <div className="flex items-center justify-between gap-3 border-t border-neutral-100 pt-3">
        <p className="text-xs text-neutral-600">
          {groups.length === 1
            ? 'Uploads as one invoice.'
            : <>
                <span className="font-semibold text-neutral-900">{groups.length} invoices</span>
                {' — '}{groups.map(describeGroup).join(' · ')}
              </>}
        </p>
        <Button onClick={() => onConfirm(groups)} disabled={busy} className="gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {groups.length === 1 ? 'Continue as one invoice' : `Continue with ${groups.length} invoices`}
        </Button>
      </div>
    </div>
  )
}
