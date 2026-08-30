/**
 * Client-side photo compression.
 *
 * Not an optimisation. Uncompressed phone photographs at the rate this module
 * will collect them run to well over a hundred gigabytes a year against a file
 * server with no lifecycle policy; compressed they are a few. Doing it in the
 * browser also means a draft holds a manageable amount while it waits for
 * signal.
 *
 * HEIC is the known gap: iOS hands it over when a photo is picked from the
 * library rather than taken, and the browser can neither draw nor decode it.
 * Those are passed through untouched and marked, rather than failing the
 * attachment.
 */
const MAX_EDGE = 1920
const QUALITY = 0.8

export interface CompressedImage {
  dataUrl: string
  bytes: number
  width: number
  height: number
  compressed: boolean
  name: string
  type: string
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('Could not read the file'))
    reader.readAsDataURL(file)
  })
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('Could not decode the image'))
    img.src = src
  })
}

export async function compressImage(file: File): Promise<CompressedImage> {
  const original = await readAsDataUrl(file)
  const passthrough: CompressedImage = {
    dataUrl: original,
    bytes: file.size,
    width: 0,
    height: 0,
    compressed: false,
    name: file.name,
    type: file.type,
  }

  // HEIC and anything else the browser cannot draw.
  if (!file.type.startsWith('image/') || /heic|heif/i.test(file.type)) {
    return passthrough
  }

  try {
    const img = await loadImage(original)
    const scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height))
    const width = Math.round(img.width * scale)
    const height = Math.round(img.height * scale)

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return passthrough
    ctx.drawImage(img, 0, 0, width, height)

    const dataUrl = canvas.toDataURL('image/jpeg', QUALITY)
    return {
      dataUrl,
      // A data URL is base64, so its byte length is about three quarters of
      // its string length.
      bytes: Math.round((dataUrl.length - dataUrl.indexOf(',') - 1) * 0.75),
      width,
      height,
      compressed: true,
      name: file.name.replace(/\.[^.]+$/, '') + '.jpg',
      type: 'image/jpeg',
    }
  } catch {
    return passthrough
  }
}
