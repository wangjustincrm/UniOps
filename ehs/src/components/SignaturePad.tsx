/**
 * Signing, either with the signature you saved once or with your finger now.
 *
 * Produces a base64 PNG either way, the same shape the visitor module already
 * stores, so a signature captured here renders anywhere in UniOps that renders
 * one. The caller cannot tell the two apart, and should not: both are the
 * person putting their name to this report.
 */
import { useEffect, useRef, useState } from 'react'
import { Eraser, PenLine, Stamp } from 'lucide-react'

export function SignaturePad({
  value,
  onChange,
  label = 'Sign with your finger',
  savedSignature = null,
}: {
  value: string | null
  onChange: (dataUrl: string | null) => void
  label?: string
  /** From the user's profile. Null when they have not saved one. */
  savedSignature?: string | null
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const drawing = useRef(false)
  const [mode, setMode] = useState<'saved' | 'draw'>('draw')
  // Kept so switching back and forth does not throw away a signature the
  // person already drew.
  const [drawn, setDrawn] = useState<string | null>(value)
  const [hasInk, setHasInk] = useState(Boolean(value))
  // The saved signature is applied at most once. Filling it in is a default,
  // not an invariant: re-applying it whenever the field goes empty would mean
  // someone who clears it to draw instead can never actually get an empty pad.
  const prefilled = useRef(false)

  useEffect(() => {
    if (prefilled.current || !savedSignature) return
    prefilled.current = true
    // A restored draft already carries a signature — leave the person's own
    // choice alone rather than overwriting it with the stored one.
    if (value) return
    setMode('saved')
    onChange(savedSignature)
    // onChange is the parent's setter and is not stable across renders;
    // including it would re-run this on every keystroke in the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedSignature])

  useEffect(() => {
    if (mode !== 'draw') return
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
    if (drawn) {
      const img = new Image()
      img.onload = () => ctx.drawImage(img, 0, 0, rect.width, rect.height)
      img.src = drawn
    }
  }, [mode, drawn])

  function pos(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  function choose(next: 'saved' | 'draw') {
    setMode(next)
    onChange(next === 'saved' ? savedSignature : drawn)
  }

  const tabCls = (on: boolean) =>
    [
      'flex min-h-[38px] flex-1 items-center justify-center gap-1.5 rounded-md px-3',
      'text-xs font-medium transition-colors',
      on
        ? 'bg-white text-primary-700 shadow-sm ring-1 ring-neutral-200'
        : 'text-neutral-600 hover:text-neutral-900',
    ].join(' ')

  return (
    <div>
      {savedSignature && (
        <div className="mb-2 flex gap-1 rounded-lg bg-neutral-100 p-1">
          <button type="button" onClick={() => choose('saved')}
                  aria-pressed={mode === 'saved'} className={tabCls(mode === 'saved')}>
            <Stamp className="h-3.5 w-3.5" aria-hidden />
            Use my saved signature
          </button>
          <button type="button" onClick={() => choose('draw')}
                  aria-pressed={mode === 'draw'} className={tabCls(mode === 'draw')}>
            <PenLine className="h-3.5 w-3.5" aria-hidden />
            Sign here instead
          </button>
        </div>
      )}

      {mode === 'saved' && savedSignature ? (
        <>
          <div className="flex h-24 w-full items-center justify-center rounded-lg border border-neutral-300 bg-white p-2">
            <img src={savedSignature} alt="Your saved signature"
                 className="max-h-full max-w-full object-contain" />
          </div>
          <p className="mt-1.5 text-xs text-neutral-500">
            Signed with the signature saved on your profile.
          </p>
        </>
      ) : (
        <>
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
              const url = e.currentTarget.toDataURL('image/png')
              setDrawn(url)
              onChange(url)
            }}
          />
          <div className="mt-1.5 flex items-center justify-between">
            <span className="text-xs text-neutral-500">
              {hasInk ? 'Signed' : label}
            </span>
            <button
              type="button"
              onClick={() => {
                const canvas = canvasRef.current
                const ctx = canvas?.getContext('2d')
                if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
                setHasInk(false)
                setDrawn(null)
                onChange(null)
              }}
              className="flex min-h-[32px] items-center gap-1.5 rounded-md px-2 text-xs font-medium text-neutral-600"
            >
              <Eraser className="h-3.5 w-3.5" aria-hidden />
              Clear
            </button>
          </div>
          {!savedSignature && (
            <p className="mt-1 text-xs text-neutral-400">
              Save a signature on your EPMS profile to skip this next time.
            </p>
          )}
        </>
      )}
    </div>
  )
}
