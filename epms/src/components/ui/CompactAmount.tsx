import { useState } from 'react'
import { createPortal } from 'react-dom'
import { formatCAD, formatCADCompact } from '@/lib/utils'

/**
 * A CAD amount shown compactly (CA$11K) to save space, with the full value
 * (CA$11,098.75) in a tooltip on hover. The tooltip is portalled to <body> with
 * fixed positioning so a scrolling table or an overflow-hidden card cannot clip
 * it. `hint` adds a line under the value (e.g. what the figure means), and
 * replaces a native `title`, which would otherwise show a second tooltip.
 */
export function CompactAmount({ value, hint, className }: {
  value: number
  hint?: string
  className?: string
}) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const compact = formatCADCompact(value)
  const full = formatCAD(value)
  const hasTip = compact !== full || !!hint

  return (
    <>
      <span
        className={className}
        onMouseEnter={hasTip ? (e) => {
          const r = e.currentTarget.getBoundingClientRect()
          setAt({ x: r.left + r.width / 2, y: r.top })
        } : undefined}
        onMouseLeave={hasTip ? () => setAt(null) : undefined}
      >
        {compact}
      </span>
      {at && createPortal(
        <div
          role="tooltip"
          className="pointer-events-none fixed z-[1000] -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md bg-neutral-900 px-2 py-1 text-xs text-white shadow-lg"
          style={{ left: at.x, top: at.y - 6 }}
        >
          <div className="font-mono font-semibold">{full}</div>
          {hint && <div className="font-sans text-[10px] text-neutral-300">{hint}</div>}
        </div>,
        document.body,
      )}
    </>
  )
}
