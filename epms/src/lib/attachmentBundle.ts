/**
 * attachmentBundle — download/zip/print helpers for the PA Document-Chain
 * attachment panel. Pure utility layer: no React, no store access. The caller
 * passes the bearer token. PDFs are rasterized to page images with pdf.js and
 * printed as <img> (never as an embedded PDF) because corporate policy blocks
 * native PDF rendering in iframes — the same reason FilePreviewPanel uses a
 * pdf.js canvas.
 */
import JSZip from 'jszip'
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

export interface BundleFile {
  filename: string
  folder: string
  fetchUrl: string
  contentType: string
  printable: boolean
}

const CONCURRENCY = 4

/** Fetch a single file as a blob with the bearer token. Throws on non-2xx. */
export async function fetchAuthedBlob(url: string, token: string | null): Promise<Blob> {
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error(`Fetch failed (${res.status}) for ${url}`)
  return res.blob()
}

/** Trigger a browser download for an already-fetched blob. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Map over items with a bounded number of concurrent workers, preserving order.
 * If fn rejects, Promise.all fails fast; callers that must not abort the batch
 * should catch inside fn (as downloadAllAsZip does).
 */
async function mapConcurrent<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const i = next++
      results[i] = await fn(items[i], i)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}

/** Ensure unique names within a ZIP folder by suffixing " (2)", " (3)", … */
function makeNamer() {
  const seen = new Map<string, number>()
  return (folder: string, filename: string): string => {
    const key = `${folder}/${filename}`
    const n = seen.get(key) ?? 0
    seen.set(key, n + 1)
    if (n === 0) return key
    const dot = filename.lastIndexOf('.')
    const stem = dot > 0 ? filename.slice(0, dot) : filename
    const ext = dot > 0 ? filename.slice(dot) : ''
    return `${folder}/${stem} (${n + 1})${ext}`
  }
}

export interface BulkResult {
  ok: number
  skipped: number
}

/**
 * Download every file, bundle into a single ZIP (folder per document), and save it.
 * Files that fail to fetch are skipped (counted in result.skipped).
 */
export async function downloadAllAsZip(
  files: BundleFile[],
  token: string | null,
  zipName: string,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkResult> {
  const zip = new JSZip()
  const namer = makeNamer()
  let done = 0
  let skipped = 0
  await mapConcurrent(files, CONCURRENCY, async (f) => {
    try {
      const blob = await fetchAuthedBlob(f.fetchUrl, token)
      zip.file(namer(f.folder, f.filename), blob)
    } catch {
      skipped++
    } finally {
      done++
      onProgress?.(done, files.length)
    }
  })
  const blob = await zip.generateAsync({ type: 'blob' })
  saveBlob(blob, zipName)
  return { ok: files.length - skipped, skipped }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(blob)
  })
}

/** Rasterize a PDF blob to one JPEG data-URL per page (memory-bounded). */
async function rasterizePdf(blob: Blob): Promise<string[]> {
  const buf = await blob.arrayBuffer()
  const pdf = await pdfjsLib.getDocument({ data: buf }).promise
  const pages: string[] = []
  try {
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n)
      const base = page.getViewport({ scale: 1 })
      // Cap the longest side at ~2000px to balance sharpness against memory.
      const scale = Math.min(2, 2000 / Math.max(base.width, base.height))
      const viewport = page.getViewport({ scale })
      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(viewport.width)
      canvas.height = Math.ceil(viewport.height)
      const ctx = canvas.getContext('2d')!
      await page.render({ canvas, canvasContext: ctx, viewport }).promise
      pages.push(canvas.toDataURL('image/jpeg', 0.85))
      canvas.width = 0        // release backing store immediately
      canvas.height = 0
      page.cleanup()
    }
  } finally {
    await pdf.destroy()
  }
  return pages
}

/** Convert one bundle file into an ordered list of printable page image data-URLs. */
async function fileToPageImages(f: BundleFile, token: string | null): Promise<string[]> {
  const blob = await fetchAuthedBlob(f.fetchUrl, token)
  if (f.contentType === 'application/pdf') return rasterizePdf(blob)
  if (f.contentType.startsWith('image/')) return [await blobToDataUrl(blob)]
  return [] // non-printable — should be filtered out before here
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ))
}

/**
 * Print one or many files as a SINGLE browser print job. PDFs and images are
 * rasterized to page images and laid out one page per printed sheet. Used for
 * both single-file print (pass one file) and print-all.
 */
export async function printFiles(
  files: BundleFile[],
  token: string | null,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkResult> {
  const printable = files.filter((f) => f.printable)
  let skipped = files.length - printable.length
  let ok = 0

  const sections: string[] = []
  let done = 0
  for (const f of printable) {
    let pages: string[] = []
    try {
      pages = await fileToPageImages(f, token)
    } catch {
      // fetch/parse failure — does not count toward ok
      skipped++
      done++
      onProgress?.(done, printable.length)
      continue
    }
    if (pages.length === 0) {
      // "printable" but produced no pages (e.g. contentType matched neither
      // pdf nor image/*) — still counts as skipped, not ok.
      skipped++
      done++
      onProgress?.(done, printable.length)
      continue
    }
    ok++
    const caption = escapeHtml(`${f.folder} · ${f.filename}`)
    for (const src of pages) {
      sections.push(
        `<figure class="pg"><img src="${src}"/><figcaption>${caption}</figcaption></figure>`,
      )
    }
    done++
    onProgress?.(done, printable.length)
  }

  if (sections.length === 0) return { ok, skipped }

  const html =
    `<!doctype html><html><head><meta charset="utf-8"><style>` +
    `@page{margin:12mm}` +
    `*{margin:0;padding:0;box-sizing:border-box}` +
    `.pg{break-after:page;text-align:center}` +
    `.pg:last-child{break-after:auto}` +
    `.pg img{max-width:100%;max-height:250mm;object-fit:contain}` +
    `.pg figcaption{font:10px/1.4 sans-serif;color:#666;padding-top:4px}` +
    `</style></head><body>${sections.join('')}</body></html>`

  await printHtmlInIframe(html)
  return { ok, skipped }
}

/** Write HTML into a hidden iframe, wait for all images to load, then print. */
async function printHtmlInIframe(html: string): Promise<void> {
  const iframe = document.createElement('iframe')
  iframe.style.position = 'fixed'
  iframe.style.right = '0'
  iframe.style.bottom = '0'
  iframe.style.width = '0'
  iframe.style.height = '0'
  iframe.style.border = '0'
  document.body.appendChild(iframe)

  let removed = false
  const remove = () => {
    if (removed) return
    removed = true
    if (iframe.parentNode) document.body.removeChild(iframe)
  }

  try {
    const doc = iframe.contentDocument!
    doc.open()
    doc.write(html)
    doc.close()

    // Wait for every <img> to finish decoding, else blank pages print.
    const imgs = Array.from(doc.images)
    await Promise.all(
      imgs.map((img) =>
        img.complete
          ? Promise.resolve()
          : new Promise<void>((res) => {
              img.onload = () => res()
              img.onerror = () => res()
            }),
      ),
    )

    iframe.contentWindow!.focus()
    iframe.contentWindow!.print()
    // Remove after the print dialog has had time to open.
    window.setTimeout(remove, 60_000)
  } catch (e) {
    remove()
    throw e
  }
}
