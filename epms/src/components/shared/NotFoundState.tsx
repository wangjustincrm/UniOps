import type { ReactNode } from 'react'
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
      <div className="text-5xl mb-4">🔍</div>
      <h2 className="text-xl font-semibold text-neutral-700">{title}</h2>
      <p className="mt-2 text-sm text-neutral-400">{message}</p>
      <BackLink to={backTo} className="mt-4">
        <Button variant="secondary">{backLabel}</Button>
      </BackLink>
    </div>
  )
}
