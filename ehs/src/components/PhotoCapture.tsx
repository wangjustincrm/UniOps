/**
 * Attaching photographs.
 *
 * `capture="environment"` asks the phone for the rear camera. It is a hint,
 * not a guarantee — the browser may still offer the library — but without it
 * the camera is never the first option, and this module is used standing in
 * front of the thing being photographed.
 *
 * Images are compressed here and held as data URLs until the form is
 * submitted, so a report filled in a Wi-Fi dead zone still has its photographs
 * when it reaches signal.
 */
import { useRef, useState } from 'react'
import { Camera, X } from 'lucide-react'
import { compressImage, type CompressedImage } from '@/lib/imageCompress'

export function PhotoCapture({
  photos,
  onChange,
  max = 8,
}: {
  photos: CompressedImage[]
  onChange: (next: CompressedImage[]) => void
  max?: number
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return
    setBusy(true)
    try {
      const room = max - photos.length
      const added: CompressedImage[] = []
      for (const file of Array.from(files).slice(0, room)) {
        added.push(await compressImage(file))
      }
      onChange([...photos, ...added])
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const full = photos.length >= max

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        className="sr-only"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <button
        type="button"
        disabled={busy || full}
        onClick={() => inputRef.current?.click()}
        className="flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 text-sm font-medium text-neutral-600 disabled:opacity-50"
      >
        <Camera className="h-4 w-4" aria-hidden />
        {busy ? 'Processing…' : full ? `Maximum ${max} photos` : 'Take a photo'}
      </button>

      {photos.length > 0 && (
        <>
          <ul className="mt-2 flex flex-wrap gap-2">
            {photos.map((p, i) => (
              <li key={`${p.name}-${i}`} className="relative">
                <img
                  src={p.dataUrl}
                  alt={p.name}
                  className="h-14 w-14 rounded-lg border border-neutral-200 object-cover"
                />
                <button
                  type="button"
                  aria-label={`Remove ${p.name}`}
                  onClick={() => onChange(photos.filter((_, j) => j !== i))}
                  className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-800 text-white"
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs text-neutral-500">
            Kept on this phone until you submit.
            {photos.some((p) => !p.compressed) &&
              ' One or more could not be compressed and will upload at full size.'}
          </p>
        </>
      )}
    </div>
  )
}
