/**
 * A scannable code for one thing, and a way to print it.
 *
 * The codes existed as tokens in the database and as a `QrCode` icon on screen
 * — a glyph meaning "this has one" — and nowhere was an actual code ever
 * drawn. The first acceptance round put it plainly: "there are no QR codes to
 * scan on any form, equipment, document, quiz, permit, or datasheet."
 *
 * Drawn by the server as SVG, because it is printed at whatever size the label
 * maker runs and a code that has gone soft is one a phone gives up on. Printed
 * at 40 mm for the same reason: the working rule is about a tenth of the
 * reading distance, and a code read at arm's length off a fire extinguisher
 * needs to be nearer a beer mat than a postage stamp.
 */
import { useEffect, useState } from 'react'
import { Printer, QrCode } from 'lucide-react'
import { apiBlob } from '@/lib/api'

interface Props {
  /** The token as printed. Null while the parent is still loading it. */
  token: string | null
  /** What scanning it opens, in the words of whoever reads the label. */
  caption: string
  /** A second line under the caption on the printed sticker — an asset number,
   *  a revision, whatever identifies this one among the others. */
  sub?: string | null
  size?: number
}

export default function QrPanel({ token, caption, sub, size = 132 }: Props) {
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!token) { setSvg(null); return }
    let dropped = false
    setFailed(false)
    // The image is proxied by ehs-api and needs the session token, which an
    // `<img src>` cannot send — so it comes back as text and is inlined.
    apiBlob(`/api/v1/qr/${encodeURIComponent(token)}/image?size=${size * 2}`)
      .then((blob) => blob.text())
      .then((text) => { if (!dropped) setSvg(text) })
      .catch(() => { if (!dropped) setFailed(true) })
    return () => { dropped = true }
  }, [token, size])

  if (!token) return null

  if (failed) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-warning-700">
        <QrCode className="h-3.5 w-3.5" aria-hidden />
        The code could not be drawn — reload, or check this still exists.
      </p>
    )
  }

  const print = () => {
    if (!svg) return
    const w = window.open('', '_blank', 'width=420,height=560')
    if (!w) return                       // pop-up blocked; the code is still on screen
    w.document.write(`<!doctype html><html><head><title>${caption}</title>
      <style>
        @page { margin: 12mm }
        body { font-family: system-ui, sans-serif; text-align: center; margin: 0 }
        .code { width: 40mm; height: 40mm; margin: 0 auto }
        .code svg { width: 100%; height: 100% }
        h1 { font-size: 11pt; margin: 4mm 0 1mm; font-weight: 600 }
        p { font-size: 9pt; margin: 0; color: #444 }
      </style></head><body>
      <div class="code">${svg}</div>
      <h1>${caption.replace(/[<>]/g, '')}</h1>
      ${sub ? `<p>${sub.replace(/[<>]/g, '')}</p>` : ''}
      </body></html>`)
    w.document.close()
    w.focus()
    w.print()
  }

  return (
    <div className="flex items-start gap-3">
      <div
        className="shrink-0 rounded-lg border border-neutral-200 bg-white p-1.5"
        style={{ width: size, height: size }}
        // Our own server's SVG, fetched with the session token. Inlined rather
        // than an object URL so it can be handed straight to the print window.
        dangerouslySetInnerHTML={{ __html: svg ?? '' }}
      />
      <div className="min-w-0">
        <p className="text-sm font-medium text-neutral-800">{caption}</p>
        {sub && <p className="mt-0.5 text-xs text-neutral-500">{sub}</p>}
        <button
          type="button"
          onClick={print}
          disabled={!svg}
          className="mt-2 inline-flex min-h-[32px] items-center gap-1.5 rounded-md border border-neutral-200 px-2.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-40"
        >
          <Printer className="h-3.5 w-3.5" aria-hidden />
          Print at 40 mm
        </button>
      </div>
    </div>
  )
}
