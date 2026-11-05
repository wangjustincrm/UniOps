import { useState, type ChangeEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useReplaceTab } from '@uniops/shell'
import { ArrowLeft, Info } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatAmount } from '@/lib/utils'
import { useUpdatePa } from '@/hooks/usePas'
import { paService, type ApiPa } from '@/services/pa'

/** Title / notes only — everything else on a Freight PA comes from its invoices. */
export function FreightPaEditForm({ pa }: { pa: ApiPa }) {
  const replaceTab = useReplaceTab(epmsRoutes)
  const queryClient = useQueryClient()
  useDocTabTitle(`Edit ${pa.pa_number}`)
  const updatePa = useUpdatePa()
  const [title, setTitle] = useState(pa.title)
  const [notes, setNotes] = useState(pa.notes ?? '')
  const [submitting, setSubmitting] = useState(false)
  const busy = updatePa.isPending || submitting

  if (!['draft', 'returned'].includes(pa.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-neutral-500">This PA cannot be edited.</p>
        <BackLink to={`/pa/${pa.id}`} className="mt-4"><Button variant="secondary">Back</Button></BackLink>
      </div>
    )
  }

  const save = async (andSubmit: boolean) => {
    if (busy || !title.trim()) return
    try {
      // Only title + notes: the backend 422s on any other field for a Freight PA.
      // Notes are sent as '' (not omitted) when cleared, so clearing sticks.
      await updatePa.mutateAsync({ id: pa.id, body: { title: title.trim(), notes: notes.trim() } })
      if (andSubmit) {
        setSubmitting(true)
        try {
          await paService.action(pa.id, { action: 'submit' })
        } catch (e) {
          alert(e instanceof Error ? e.message : 'Could not submit the PA')
          return
        } finally {
          setSubmitting(false)
        }
        await queryClient.invalidateQueries({ queryKey: ['pas'] })
        await queryClient.invalidateQueries({ queryKey: ['tasks'] })
      }
      replaceTab(`/pa/${pa.id}`)
    } catch {
      // useUpdatePa alerts the server error
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-3">
        <button onClick={() => replaceTab(`/pa/${pa.id}`)} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Freight PA</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            {pa.pa_number} · {pa.status === 'returned' ? 'Returned — revise and resubmit' : 'Draft'} · {formatAmount(Number(pa.payment_amount), pa.currency)}
          </p>
        </div>
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-primary-100 bg-primary-50 px-4 py-3 text-xs text-primary-800">
        <Info className="h-4 w-4 shrink-0 mt-px" />
        <span>
          Amounts, invoices and the cost-center split come from the courier invoices on the Freight page.
          To change them, cancel this PA, reopen the invoice there, fix the matching and raise a new PA.
        </span>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Title *</label>
          <Input value={title} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} maxLength={255} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Notes</label>
          <textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)}
            className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => replaceTab(`/pa/${pa.id}`)} disabled={busy}>Cancel</Button>
        <Button variant="secondary" onClick={() => save(false)} disabled={busy || !title.trim()}>
          {updatePa.isPending && !submitting ? 'Saving…' : 'Save Draft'}
        </Button>
        <Button onClick={() => save(true)} disabled={busy || !title.trim()}>
          {submitting ? 'Submitting…' : 'Save & Submit'}
        </Button>
      </div>
    </div>
  )
}
