// crypto.randomUUID exists only in secure contexts (HTTPS or localhost). The dev
// servers are reached over plain http://10.10.50.64:<port>, where it is undefined
// and every page that mints a client-side id (New PR/PO, line items…) crashes.
// crypto.getRandomValues is available everywhere, so build an RFC 4122 v4 UUID
// from it. No-op in production (HTTPS), where the native function exists.
if (typeof crypto !== 'undefined' && typeof crypto.randomUUID !== 'function') {
  crypto.randomUUID = function randomUUID() {
    const b = crypto.getRandomValues(new Uint8Array(16))
    b[6] = (b[6] & 0x0f) | 0x40
    b[8] = (b[8] & 0x3f) | 0x80
    const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}` as `${string}-${string}-${string}-${string}-${string}`
  }
}

export {}
