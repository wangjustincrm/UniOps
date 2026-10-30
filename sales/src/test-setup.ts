import '@testing-library/jest-dom/vitest'

// jsdom lacks what Radix Select / Checkbox measure and capture with. Tests
// only: the browser has all of these.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver
const proto = Element.prototype as Element & {
  hasPointerCapture?: (id: number) => boolean
  releasePointerCapture?: (id: number) => void
  scrollIntoView?: () => void
}
proto.hasPointerCapture ??= () => false
proto.releasePointerCapture ??= () => {}
proto.scrollIntoView ??= () => {}
