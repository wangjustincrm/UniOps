import type { ReactNode } from 'react'
import { SearchX } from 'lucide-react'
import { BackLink } from '@/components/BackLink'
import { Button } from '@/components/ui/button'

/** The "<document> Not Found" screen, in the layout the PR / PO / Agreement
 *  detail pages already use — the GR, invoice, receipt and PA pages printed a
 *  single grey line instead (ISS-0183). */
export function NotFoundState({ title, message, backTo, backLabel }: {
  title: string
  message: ReactNode
  backTo: string
  backLabel: string
}) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <SearchX aria-hidden className="mb-4 h-10 w-10 stroke-[1.5] text-fg-faint" />
      <h2 className="text-xl font-semibold text-fg-secondary">{title}</h2>
      <p className="mt-2 text-sm text-fg-muted">{message}</p>
      <BackLink to={backTo} className="mt-4">
        <Button variant="secondary">{backLabel}</Button>
      </BackLink>
    </div>
  )
}
