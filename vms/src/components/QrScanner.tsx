/** Live QR camera scanner (PRD §2.4.2 VMS-CO-003).
 *
 * Wraps `qr-scanner` (Nicolas Fagiola's lib). We pass the worker path
 * explicitly so Vite resolves it correctly in both dev and build modes.
 *
 * Browser support: requires HTTPS (or localhost). Calls `getUserMedia` and
 * needs camera permission. On iOS, `playsInline` is mandatory or Safari
 * fills the entire screen instead of inlining the video.
 */
import { useEffect, useRef, useState } from 'react'
import QrScannerLib from 'qr-scanner'
import { AlertCircle, Camera, RefreshCcw } from 'lucide-react'

interface Props {
  onDecode: (text: string) => void
  /** Suppresses the same payload firing repeatedly. Defaults to 1500ms. */
  cooldownMs?: number
}

export function QrScanner({ onDecode, cooldownMs = 1500 }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const scannerRef = useRef<QrScannerLib | null>(null)
  const lastResultRef = useRef<{ data: string; at: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  // (re)start scanner whenever the video element is mounted.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let cancelled = false
    setError(null)

    const scanner = new QrScannerLib(
      video,
      (result) => {
        const data = result.data
        const now = Date.now()
        const last = lastResultRef.current
        if (last && last.data === data && now - last.at < cooldownMs) return
        lastResultRef.current = { data, at: now }
        onDecode(data)
      },
      {
        // Slightly slower but consistent across iOS / Android / desktop webcams.
        highlightScanRegion: true,
        highlightCodeOutline: true,
        preferredCamera: 'environment',
        returnDetailedScanResult: true,
        maxScansPerSecond: 4,
      },
    )
    scannerRef.current = scanner

    scanner.start().then(() => {
      if (!cancelled) setRunning(true)
    }).catch((err: unknown) => {
      if (cancelled) return
      const msg = err instanceof Error ? err.message : String(err)
      setError(
        msg.toLowerCase().includes('denied')
          ? 'Camera permission denied. Allow access in your browser settings to scan.'
          : `Could not start camera: ${msg}`,
      )
    })

    return () => {
      cancelled = true
      scanner.stop()
      scanner.destroy()
      scannerRef.current = null
    }
    // We intentionally only re-init on mount/unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="rounded-md border border-neutral-200 bg-neutral-900 overflow-hidden">
      <div className="relative aspect-square w-full max-w-sm mx-auto">
        {/* `playsInline` is critical for iOS Safari. */}
        <video
          ref={videoRef}
          className="h-full w-full object-cover"
          muted
          playsInline
        />
        {!running && !error && (
          <div className="absolute inset-0 flex items-center justify-center text-white/80 text-sm bg-black/30">
            <Camera className="mr-2 h-4 w-4" />
            Starting camera…
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 bg-danger-50 px-3 py-2 text-sm text-danger-600">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Camera unavailable</p>
            <p className="text-xs">{error}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-1 inline-flex items-center gap-1 text-xs text-danger-600 hover:underline"
            >
              <RefreshCcw className="h-3 w-3" />
              Reload page to retry
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
