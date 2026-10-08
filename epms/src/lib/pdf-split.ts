// Splitting one uploaded PDF into one file per invoice.
//
// Vendors send a month of invoices as a single PDF — the sample that prompted
// this (Culligan Water, 2026-09) is six invoices, one to a page, every page a
// complete document down to its own remittance stub. Uploaded whole, that is
// one invoice in EPMS for the price of six, and the other five are never paid.
//
// The cuts are made by a person, in the preview, before anything is extracted.
// Nothing here guesses at boundaries: a mis-cut merges two invoices into one or
// halves a single one, and both failures are invisible downstream — the same
// class of silent loss the total check exists to stop. A person can see in one
// glance what no heuristic can prove.
//
// Pages are copied, not re-rendered: pdf-lib lifts the page objects across, so
// each slice is byte-faithful to what the vendor sent, which is what makes it
// fit to keep as the invoice's attachment.
//
// pdf-lib is imported at the point of use, not at the top. Statically it adds
// ~430 KB to the entry chunk — paid by every page of EPMS on every load, for a
// library that only ever runs when someone picks a PDF off the invoice upload
// form. The dynamic import puts it in a chunk of its own, fetched then.

/** Page numbers making up one invoice, 1-based — as printed, and as a person counts. */
export type PageGroup = number[]

/** Above this, the strip of thumbnails stops being something anyone can read. */
export const MAX_SPLITTABLE_PAGES = 50

/**
 * How many pages the PDF has, or null when that cannot be established.
 *
 * Null covers "not a PDF" and "pdf-lib could not parse it", and both mean the
 * same thing to the caller: carry on with the upload exactly as before. A file
 * the splitter cannot read must never be a file the user cannot upload.
 */
export async function readPageCount(file: File): Promise<number | null> {
  if (file.type !== 'application/pdf') return null
  try {
    const { PDFDocument } = await import('pdf-lib')
    const doc = await PDFDocument.load(await file.arrayBuffer(), { ignoreEncryption: true })
    return doc.getPageCount()
  } catch {
    return null
  }
}

/** Turn "cut after page N" marks into the runs of pages they delimit. */
export function groupsFromCuts(pageCount: number, cutsAfter: ReadonlySet<number>): PageGroup[] {
  const groups: PageGroup[] = []
  let current: PageGroup = []
  for (let page = 1; page <= pageCount; page++) {
    current.push(page)
    if (cutsAfter.has(page) || page === pageCount) {
      groups.push(current)
      current = []
    }
  }
  return groups
}

/** "pages 1–2" / "page 4" — how a group is named on screen. */
export function describeGroup(group: PageGroup): string {
  if (group.length === 0) return ''
  if (group.length === 1) return `page ${group[0]}`
  return `pages ${group[0]}–${group[group.length - 1]}`
}

/** The slice's file name carries where it came from: "Invoices Aug p3-4.pdf". */
export function sliceName(originalName: string, group: PageGroup): string {
  const stem = originalName.replace(/\.pdf$/i, '')
  const span = group.length === 1
    ? `p${group[0]}`
    : `p${group[0]}-${group[group.length - 1]}`
  return `${stem} ${span}.pdf`
}

/**
 * Cut the PDF into one file per group, in the order given.
 *
 * A single group covering every page is returned as the original file
 * untouched — re-saving a one-invoice PDF through pdf-lib would rewrite bytes
 * the vendor signed off on, for no gain.
 */
export async function splitPdf(file: File, groups: PageGroup[]): Promise<File[]> {
  const total = groups.reduce((n, g) => n + g.length, 0)
  if (groups.length <= 1 && total > 0) return [file]

  const { PDFDocument } = await import('pdf-lib')
  const source = await PDFDocument.load(await file.arrayBuffer(), { ignoreEncryption: true })
  const out: File[] = []
  for (const group of groups) {
    const doc = await PDFDocument.create()
    const pages = await doc.copyPages(source, group.map((p) => p - 1))
    pages.forEach((p) => doc.addPage(p))
    const bytes = await doc.save()
    // Copied into a plain ArrayBuffer: pdf-lib hands back Uint8Array<ArrayBufferLike>,
    // which a BlobPart will not accept — the buffer behind it may be a SharedArrayBuffer.
    const buffer = new ArrayBuffer(bytes.byteLength)
    new Uint8Array(buffer).set(bytes)
    out.push(new File([buffer], sliceName(file.name, group), { type: 'application/pdf' }))
  }
  return out
}
