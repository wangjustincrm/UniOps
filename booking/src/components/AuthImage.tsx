/**
 * AuthImage — renders a file-api image with Bearer-token authentication.
 *
 * file-api GET /files/v1/files/{id} requires a CurrentUser (Authorization header).
 * Browsers do not send Authorization headers for <img src>, so this component
 * fetches the file with the token, creates a blob object URL, and feeds that
 * to a plain <img>. The object URL is revoked on unmount to avoid memory leaks.
 * Any fetch error (401, 404, network) causes the image to be hidden gracefully.
 */
import { useEffect, useRef, useState } from 'react'
import { getToken } from '@/lib/api'

interface AuthImageProps {
  /** Absolute URL to the file-api download endpoint */
  src: string
  alt?: string
  className?: string
}

export function AuthImage({ src, alt = '', className }: AuthImageProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const prevUrl = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setFailed(false)
      const token = getToken()
      try {
        const resp = await fetch(src, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!resp.ok) {
          if (!cancelled) setFailed(true)
          return
        }
        const blob = await resp.blob()
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        prevUrl.current = url
        setObjectUrl(url)
      } catch {
        if (!cancelled) setFailed(true)
      }
    }

    load()

    return () => {
      cancelled = true
      if (prevUrl.current) {
        URL.revokeObjectURL(prevUrl.current)
        prevUrl.current = null
      }
    }
  }, [src])

  if (failed || !objectUrl) return null

  return <img src={objectUrl} alt={alt} className={className} />
}
