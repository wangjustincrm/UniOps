import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Dialog, ErrorMessage, Skeleton } from '@/platform-ui'
import { fetchEligibility } from './ordersApi'

/** AC-OB-21: the orders the next cycle would send a 940 for — a dry run
 *  (nothing is written, nothing is sent). */
export function NextSends() {
  const [open, setOpen] = useState(false)
  const q = useQuery({ queryKey: ['sales', 'orders', 'eligibility-preview'], queryFn: fetchEligibility, enabled: open, staleTime: 0 })
  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>Next 940s</Button>
      <Dialog open={open} onOpenChange={setOpen} title="Next 940s"
        description="Orders that qualify for a 940 now: the next cycle queues these. Nothing is sent from here.">
        {q.isLoading ? <Skeleton className="h-20 w-full" /> : q.error ? <ErrorMessage error={q.error} /> : (
          q.data && q.data.length > 0 ? (
            <ul className="max-h-80 overflow-auto text-sm">
              {q.data.map((e) => <li key={e.order_id}>Order {e.order_number} · {e.partner_code} {e.source_order_number ?? ''}</li>)}
            </ul>
          ) : <p className="text-sm text-neutral-500">No order qualifies right now.</p>
        )}
      </Dialog>
    </>
  )
}
