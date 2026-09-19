import { useEffect, useMemo, useState } from 'react'
import { Loader2, Scissors, AlertTriangle } from 'lucide-react'
import { pdfjsLib } from '@/lib/pdfjs'
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

  useEffect(() => {
    let cancelled = false
    setThumbs([]); setFailed(false); setPageCount(0); setCutsAfter(new Set())

    ;(async () => {
      const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise
      if (cancelled) { pdf.destroy(); return }
      setPageCount(pdf.numPages)

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
      pdf.destroy()
    })().catch(() => { if (!cancelled) setFailed(true) })

    return () => { cancelled = true }
  }, [file])

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
          Click the scissors between two pages to start a new invoice there. Each
          invoice is then read, checked and created on its own, with its own pages
          attached.
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
                      <div className={cn(
                        'flex h-[134px] w-[100px] items-center justify-center overflow-hidden rounded border bg-white',
                        groupIndex % 2 === 0 ? 'border-primary-200' : 'border-neutral-300',
                      )}>
                        {thumbs[page - 1]
                          ? <img src={thumbs[page - 1]} alt={`Page ${page}`} className="max-h-full max-w-full" />
                          : <Loader2 className="h-4 w-4 animate-spin text-neutral-300" />}
                      </div>
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
