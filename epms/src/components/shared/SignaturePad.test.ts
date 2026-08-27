/**
 * cropRect — the arithmetic that decides how much of the pad becomes the
 * signature.
 *
 * Worth pinning because every way it fails is silent: the bounding box is
 * tracked in CSS pixels while the canvas is sized in device pixels, so a
 * missing or doubled ratio yields a crop at the wrong scale, and an unclamped
 * padded box makes drawImage return a partly blank image. All of those still
 * produce a valid PNG — just the wrong one.
 */
import { describe, expect, it } from 'vitest'

import { cropRect } from './SignaturePad'

// A 600x160 CSS pad on a 2x display.
const W = 1200
const H = 320

describe('cropRect', () => {
  it('returns null when nothing has been drawn', () => {
    expect(cropRect(null, W, H, 2)).toBeNull()
  })

  it('converts CSS-pixel bounds to device pixels exactly once', () => {
    const rect = cropRect({ minX: 100, minY: 40, maxX: 200, maxY: 90 }, W, H, 2, 0)
    expect(rect).toEqual({ sx: 200, sy: 80, sw: 200, sh: 100 })
  })

  it('pads around the stroke so the ink is not clipped', () => {
    const rect = cropRect({ minX: 100, minY: 40, maxX: 200, maxY: 90 }, W, H, 1, 6)
    expect(rect).toEqual({ sx: 94, sy: 34, sw: 112, sh: 62 })
  })

  it('clamps to the top-left corner without shrinking the far edge', () => {
    // Padding pushes the origin negative; the clamp must not also pull the
    // right/bottom edge in, which is what deriving width from the box alone
    // would do.
    const rect = cropRect({ minX: 2, minY: 1, maxX: 50, maxY: 30 }, W, H, 1, 6)
    expect(rect).toEqual({ sx: 0, sy: 0, sw: 56, sh: 36 })
  })

  it('clamps to the bottom-right corner', () => {
    const rect = cropRect({ minX: 580, minY: 150, maxX: 600, maxY: 160 }, W, H, 2, 6)
    expect(rect?.sx).toBe(1148)
    expect(rect?.sy).toBe(288)
    // (600 + 6) * 2 = 1212 exceeds the 1200-wide backing store.
    expect(rect!.sx + rect!.sw).toBe(W)
    expect(rect!.sy + rect!.sh).toBe(H)
  })

  it('keeps a single-point tap croppable', () => {
    const rect = cropRect({ minX: 300, minY: 80, maxX: 300, maxY: 80 }, W, H, 1, 6)
    expect(rect).toEqual({ sx: 294, sy: 74, sw: 12, sh: 12 })
  })

  it('refuses a nonsensical ratio rather than emitting a zero-size crop', () => {
    expect(cropRect({ minX: 10, minY: 10, maxX: 20, maxY: 20 }, W, H, 0)).toBeNull()
  })
})
