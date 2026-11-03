import { ExternalLink } from 'lucide-react'
import { CopyChip } from './Copy'

/** A tracking number (the user, 2026-11-03): the number as a copy chip and,
 *  when the carrier has a tracking link (Configuration → Carriers; the server
 *  builds it, the number URL-encoded), an icon opening the carrier's page in a
 *  new tab. An unknown carrier: the copy chip only. */
export function TrackingLink({ number, url }: { number: string; url?: string | null }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <CopyChip text={number} />
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Track ${number} on the carrier's site (new tab)`} title="Track on the carrier's site"
          className="inline-flex h-6 w-6 items-center justify-center rounded text-primary-700 hover:bg-primary-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <ExternalLink aria-hidden className="h-3.5 w-3.5" />
        </a>
      )}
    </span>
  )
}
