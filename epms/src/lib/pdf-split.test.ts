import { describe, expect, it } from 'vitest'
import {
  describeGroup, groupsFromCuts, readPageCount, sliceName, splitPdf,
} from './pdf-split'

// A vendor's monthly PDF holding several complete invoices. Cutting it wrong is
// silent in both directions — two invoices merged means the second is never
// paid, one invoice halved means half of it is never paid — so what the cuts
// produce is pinned here rather than eyeballed in the browser.

/** A PDF with `pages` pages, each stamped so a slice can be identified. */
async function makePdf(pages: number, name = 'Invoices Aug.pdf'): Promise<File> {
  const { PDFDocument, StandardFonts } = await import('pdf-lib')
  const doc = await PDFDocument.create()
  const font = await doc.embedFont(StandardFonts.Helvetica)
  for (let i = 1; i <= pages; i++) {
    doc.addPage([220, 220]).drawText(`INVOICE ${i}`, { x: 20, y: 100, size: 12, font })
  }
  const bytes = await doc.save()
  const buffer = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(buffer).set(bytes)
  return new File([buffer], name, { type: 'application/pdf' })
}

async function pageCountOf(file: File): Promise<number> {
  const { PDFDocument } = await import('pdf-lib')
  return (await PDFDocument.load(await file.arrayBuffer())).getPageCount()
}

describe('groupsFromCuts', () => {
  it('turns a cut after every page into one invoice per page', () => {
    // The real sample that prompted this: six Culligan invoices, one to a page.
    expect(groupsFromCuts(6, new Set([1, 2, 3, 4, 5])))
      .toEqual([[1], [2], [3], [4], [5], [6]])
  })

  it('turns cuts every two pages into two-page invoices', () => {
    expect(groupsFromCuts(6, new Set([2, 4]))).toEqual([[1, 2], [3, 4], [5, 6]])
  })

  it('treats no cuts as a single invoice, which is what upload always did', () => {
    expect(groupsFromCuts(6, new Set())).toEqual([[1, 2, 3, 4, 5, 6]])
  })

  it('handles uneven invoices — the shape no "every N pages" rule can express', () => {
    expect(groupsFromCuts(6, new Set([1, 4]))).toEqual([[1], [2, 3, 4], [5, 6]])
  })

  it('ignores a cut after the last page rather than emitting an empty invoice', () => {
    expect(groupsFromCuts(3, new Set([3]))).toEqual([[1, 2, 3]])
  })
})

describe('naming', () => {
  it('names a slice after the pages it came from', () => {
    expect(sliceName('Invoices Aug.pdf', [3])).toBe('Invoices Aug p3.pdf')
    expect(sliceName('Invoices Aug.pdf', [3, 4])).toBe('Invoices Aug p3-4.pdf')
  })

  it('does not leave the original .pdf in the middle of the name', () => {
    expect(sliceName('scan.PDF', [1])).toBe('scan p1.pdf')
  })

  it('describes a group the way the panel shows it', () => {
    expect(describeGroup([4])).toBe('page 4')
    expect(describeGroup([4, 5, 6])).toBe('pages 4–6')
  })
})

describe('readPageCount', () => {
  it('reads the page count of a PDF', async () => {
    expect(await readPageCount(await makePdf(6))).toBe(6)
  })

  it('returns null for a non-PDF so the upload carries on untouched', async () => {
    const jpg = new File([new ArrayBuffer(8)], 'scan.jpg', { type: 'image/jpeg' })
    expect(await readPageCount(jpg)).toBeNull()
  })

  it('returns null for a PDF it cannot parse — unreadable must not mean unuploadable', async () => {
    const broken = new File([new TextEncoder().encode('%PDF-1.4 not really')],
      'broken.pdf', { type: 'application/pdf' })
    expect(await readPageCount(broken)).toBeNull()
  })
})

describe('splitPdf', () => {
  it('cuts six one-page invoices into six files, in order', async () => {
    const slices = await splitPdf(await makePdf(6), groupsFromCuts(6, new Set([1, 2, 3, 4, 5])))

    expect(slices).toHaveLength(6)
    expect(slices.map((f) => f.name)).toEqual([
      'Invoices Aug p1.pdf', 'Invoices Aug p2.pdf', 'Invoices Aug p3.pdf',
      'Invoices Aug p4.pdf', 'Invoices Aug p5.pdf', 'Invoices Aug p6.pdf',
    ])
    for (const slice of slices) expect(await pageCountOf(slice)).toBe(1)
  })

  it('keeps multi-page invoices whole', async () => {
    const slices = await splitPdf(await makePdf(6), [[1, 2], [3, 4, 5], [6]])

    expect(slices.map((f) => f.name)).toEqual([
      'Invoices Aug p1-2.pdf', 'Invoices Aug p3-5.pdf', 'Invoices Aug p6.pdf',
    ])
    expect(await Promise.all(slices.map(pageCountOf))).toEqual([2, 3, 1])
  })

  it('returns the original file untouched when it is one invoice', async () => {
    const file = await makePdf(3)
    const slices = await splitPdf(file, [[1, 2, 3]])
    // Identity, not an equal copy: re-saving through pdf-lib would rewrite bytes
    // the vendor sent, for no gain, on the case that has always worked.
    expect(slices).toHaveLength(1)
    expect(slices[0]).toBe(file)
  })

  it('loses no page — every page of the source lands in exactly one slice', async () => {
    const groups = [[1], [2, 3], [4, 5, 6]]
    const slices = await splitPdf(await makePdf(6), groups)
    const pagesOut = (await Promise.all(slices.map(pageCountOf))).reduce((a, b) => a + b, 0)
    expect(pagesOut).toBe(6)
  })
})
