/**
 * Signature pad — draw with a mouse, stylus or finger, export a trimmed PNG.
 *
 * Deliberately dependency-free. VMS captures visitor signatures with
 * react-signature-canvas, but EPMS's lockfile is already one package out of
 * sync with its manifest, and an image built with `npm ci` fails outright on a
 * lockfile that cannot resolve. A hundred lines of pointer handling is not
 * worth putting a release at that risk.
 *
 * Strokes are exported cropped to their own bounding box. A signature drawn
 * small in the middle of the pad would otherwise carry the surrounding blank
 * space into the PDF, where the whole image is scaled to the signature line —
 * the ink would come out a fraction of its intended size.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'

export interface SignaturePadHandle {
  isEmpty(): boolean
  /** Trimmed PNG data URL, or null when nothing has been drawn. */
  toDataURL(): string | null
  clear(): void
}

export interface Bounds { minX: number; minY: number; maxX: number; maxY: number }

export interface CropRect { sx: number; sy: number; sw: number; sh: number }

/**
 * Source rectangle to copy out of the backing store, in device pixels.
 *
 * Split out and exported because this is the part that goes wrong quietly: the
 * bounding box is tracked in CSS pixels while the canvas is sized in device
 * pixels, so the ratio has to be applied exactly once, and the padded box has
 * to be clamped to the canvas or drawImage silently yields a partly blank crop.
 * Returns null when there is nothing to copy.
 */
export function cropRect(
  bounds: Bounds | null,
  canvasWidth: number,
  canvasHeight: number,
  ratio: number,
  pad = 6,
): CropRect | null {
  if (!bounds || ratio <= 0) return null
  const sx = Math.max(0, (bounds.minX - pad) * ratio)
  const sy = Math.max(0, (bounds.minY - pad) * ratio)
  // Width is measured from the padded LEFT edge, which clamping may have moved
  // to 0 — deriving it from the box width alone would overshoot by the amount
  // that was clamped away.
  const right = Math.min(canvasWidth, (bounds.maxX + pad) * ratio)
  const bottom = Math.min(canvasHeight, (bounds.maxY + pad) * ratio)
  const sw = right - sx
  const sh = bottom - sy
  if (sw <= 0 || sh <= 0) return null
  return { sx, sy, sw, sh }
}

interface SignaturePadProps {
  /** CSS height of the drawing surface, in pixels. */
  height?: number
  onChange?: (isEmpty: boolean) => void
}

export const SignaturePad = forwardRef<SignaturePadHandle, SignaturePadProps>(
  function SignaturePad({ height = 160, onChange }, ref) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null)
    const drawing = useRef(false)
    const bounds = useRef<Bounds | null>(null)
    const ratio = useRef(1)
    const [empty, setEmpty] = useState(true)

    // Sized once, on mount. Resizing the backing store clears it, so reacting
    // to window resizes would wipe a signature mid-stroke.
    useEffect(() => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      ratio.current = dpr
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.scale(dpr, dpr)
      ctx.lineWidth = 2
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.strokeStyle = '#111827'
    }, [])

    const setEmptyState = (value: boolean) => {
      setEmpty(value)
      onChange?.(value)
    }

    const pointAt = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }

    const track = (x: number, y: number) => {
      const b = bounds.current
      bounds.current = b === null
        ? { minX: x, minY: y, maxX: x, maxY: y }
        : {
            minX: Math.min(b.minX, x), minY: Math.min(b.minY, y),
            maxX: Math.max(b.maxX, x), maxY: Math.max(b.maxY, y),
          }
    }

    const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const ctx = canvasRef.current?.getContext('2d')
      if (!ctx) return
      // Capture so a stroke that leaves the canvas still ends cleanly.
      e.currentTarget.setPointerCapture(e.pointerId)
      const { x, y } = pointAt(e)
      drawing.current = true
      ctx.beginPath()
      ctx.moveTo(x, y)
      // A tap with no movement should still leave a mark.
      ctx.lineTo(x, y)
      ctx.stroke()
      track(x, y)
      if (empty) setEmptyState(false)
    }

    const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!drawing.current) return
      const ctx = canvasRef.current?.getContext('2d')
      if (!ctx) return
      const { x, y } = pointAt(e)
      ctx.lineTo(x, y)
      ctx.stroke()
      track(x, y)
    }

    const endStroke = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!drawing.current) return
      drawing.current = false
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    }

    const clear = () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
      bounds.current = null
      setEmptyState(true)
    }

    useImperativeHandle(ref, () => ({
      isEmpty: () => bounds.current === null,
      clear,
      toDataURL: () => {
        const canvas = canvasRef.current
        if (!canvas) return null
        const rect = cropRect(bounds.current, canvas.width, canvas.height, ratio.current)
        if (!rect) return null
        const out = document.createElement('canvas')
        out.width = Math.round(rect.sw)
        out.height = Math.round(rect.sh)
        const ctx = out.getContext('2d')
        if (!ctx) return null
        ctx.drawImage(canvas, rect.sx, rect.sy, rect.sw, rect.sh, 0, 0, out.width, out.height)
        return out.toDataURL('image/png')
      },
    }))

    return (
      <div className="flex flex-col gap-2">
        <canvas
          ref={canvasRef}
          style={{ height, touchAction: 'none' }}
          className="w-full rounded-lg border border-dashed border-neutral-300 bg-white"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endStroke}
          onPointerLeave={endStroke}
          onPointerCancel={endStroke}
        />
        <div className="flex items-center justify-between">
          <p className="text-xs text-neutral-400">
            {empty ? 'Sign inside the box above.' : 'Looks right? Save it below.'}
          </p>
          <button
            type="button"
            onClick={clear}
            disabled={empty}
            className="text-xs font-medium text-neutral-500 hover:text-neutral-700 disabled:opacity-40"
          >
            Clear
          </button>
        </div>
      </div>
    )
  },
)
