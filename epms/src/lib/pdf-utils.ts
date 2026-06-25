// We use html2canvas + jsPDF directly (both installed as transitive deps of html2pdf.js).
// Bypassing html2pdf's own wrapper avoids two known issues:
//   1. Its internal overlay has "overflow:hidden" which clips content taller than viewport.
//   2. It calls container.firstChild for rendering — if that's an HTML comment node the
//      capture returns a blank canvas.
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'

const A4_W = 210   // mm
const A4_H = 297   // mm
const MARGIN = 10  // mm

export async function downloadPdf(html: string, filename: string): Promise<void> {
  // ── 1. Parse the generated HTML document ─────────────────────────────────
  const parser = new DOMParser()
  const parsed = parser.parseFromString(html, 'text/html')
  parsed.querySelector('.no-print')?.remove()

  // ── 2. Inject styles into <head> ─────────────────────────────────────────
  // html2canvas reads window.getComputedStyle(), which only picks up rules from
  // <head> stylesheets.  A <style> inside a <div> is ignored by the engine.
  // We also rename  body { … }  →  .epms-pdf-root { … }  so body rules apply
  // to our container div without polluting the real document body.
  const rawCss = parsed.querySelector('style')?.textContent ?? ''
  const patchedCss = rawCss.replace(/\bbody\b\s*\{/g, '.epms-pdf-root {')
  const styleEl = document.createElement('style')
  styleEl.textContent = patchedCss
  document.head.appendChild(styleEl)

  // ── 3. Build the container and add it to the live DOM ────────────────────
  // html2canvas requires the element to be rendered (not display:none / off-screen).
  // position:fixed + z-index puts it on top; pointer-events:none prevents interaction.
  const container = document.createElement('div')
  container.className = 'epms-pdf-root'
  container.innerHTML = parsed.body.innerHTML  // innerHTML — NOT firstChild, avoids comment node issue
  container.style.cssText = [
    'position:fixed',
    'top:0',
    'left:0',
    'width:860px',
    'background:#fff',
    'z-index:99999',
    'pointer-events:none',
  ].join(';')
  document.body.appendChild(container)

  // ── 4. Wait for CSSOM and layout to stabilise ────────────────────────────
  await new Promise<void>(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  )

  try {
    // ── 5. Capture to canvas ───────────────────────────────────────────────
    const canvas = await html2canvas(container, {
      scale: 2,
      useCORS: true,
      logging: false,
      scrollY: 0,
      windowWidth: 860,
    })

    const imgW = canvas.width   // px  (scale:2 so actual content width = 430px)
    const imgH = canvas.height  // px

    // ── 6. Slice into A4 pages and assemble PDF ────────────────────────────
    const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' })

    const contentW = A4_W - MARGIN * 2   // 190 mm
    const contentH = A4_H - MARGIN * 2   // 277 mm

    // Canvas pixels that fill one content column → 1 mm conversion factor
    const pxPerMm = imgW / contentW
    const pageHpx = contentH * pxPerMm  // canvas rows per A4 page

    const totalPages = Math.ceil(imgH / pageHpx)

    for (let p = 0; p < totalPages; p++) {
      if (p > 0) pdf.addPage()

      const srcY = Math.round(p * pageHpx)
      const srcH = Math.min(Math.ceil(pageHpx), imgH - srcY)

      // Copy the page slice to a temporary canvas
      const slice = document.createElement('canvas')
      slice.width  = imgW
      slice.height = srcH
      slice.getContext('2d')!.drawImage(
        canvas,
        0, srcY, imgW, srcH,
        0, 0,    imgW, srcH,
      )

      pdf.addImage(
        slice.toDataURL('image/jpeg', 0.98),
        'JPEG',
        MARGIN, MARGIN,
        contentW,
        srcH / pxPerMm,  // mm height of this slice
      )
    }

    pdf.save(filename)
  } finally {
    document.body.removeChild(container)
    document.head.removeChild(styleEl)
  }
}
