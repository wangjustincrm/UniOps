/**
 * Signing with a finger.
 *
 * Produces a base64 PNG, the same shape the visitor module already stores, so
 * a signature captured here renders anywhere in UniOps that renders one.
 */
import { useEffect, useRef, useState } from 'react'
import { Eraser } from 'lucide-react'

export function SignaturePad({
  value,
  onChange,
  label = 'Sign with your finger',
}: {
  value: string | null
  onChange: (dataUrl: string | null) => void
  label?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const drawing = useRef(false)
  const [hasInk, setHasInk] = useState(Boolean(value))

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    // Match the backing store to the display size so strokes are not blurred
    // or offset on a high-density screen.
    const ratio = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    canvas.width = rect.width * ratio
    canvas.height = rect.height * ratio
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(ratio, ratio)
    ctx.lineWidth = 2
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.strokeStyle = '#1A2730'
    if (value) {
      const img = new Image()
      img.onload = () => ctx.drawImage(img, 0, 0, rect.width, rect.height)
      img.src = value
    }
  }, [value])

  function pos(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  return (
    <div>
      <canvas
        ref={canvasRef}
        aria-label={label}
        className="h-24 w-full touch-none rounded-lg border border-neutral-300 bg-white"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          const ctx = e.currentTarget.getContext('2d')
          if (!ctx) return
          const { x, y } = pos(e)
          ctx.beginPath()
          ctx.moveTo(x, y)
          drawing.current = true
        }}
        onPointerMove={(e) => {
          if (!drawing.current) return
          const ctx = e.currentTarget.getContext('2d')
          if (!ctx) return
          const { x, y } = pos(e)
          ctx.lineTo(x, y)
          ctx.stroke()
          setHasInk(true)
        }}
        onPointerUp={(e) => {
          drawing.current = false
          onChange(e.currentTarget.toDataURL('image/png'))
        }}
      />
      <div className="mt-1.5 flex items-center justify-between">
        <span className="text-xs text-neutral-500">{hasInk ? 'Signed' : label}</span>
        <button
          type="button"
          onClick={() => {
            const canvas = canvasRef.current
            const ctx = canvas?.getContext('2d')
            if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
            setHasInk(false)
            onChange(null)
          }}
          className="flex min-h-[32px] items-center gap-1.5 rounded-md px-2 text-xs font-medium text-neutral-600"
        >
          <Eraser className="h-3.5 w-3.5" aria-hidden />
          Clear
        </button>
      </div>
    </div>
  )
}
