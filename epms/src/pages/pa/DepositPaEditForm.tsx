import { useState, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Alert, Card, FormField, IconButton, Textarea, useReplaceTab } from '@uniops/shell'
import { ArrowLeft } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatAmount } from '@/lib/utils'
import { useUpdatePa } from '@/hooks/usePas'
import { paService, type ApiPa } from '@/services/pa'

/**
 * The deposit PA of a OneTime (Deposit) agreement. Its amount is the
 * agreement's deposit and it has no invoice, PO or tax, so only the title,
 * the expected refund date and the notes can change — exactly what epms-api
 * accepts on PATCH for it (pa.py update_pa). The PO/agreement editor would
 * recompute the amount from a PO % it does not have and blank it (found in
 * DEV1 2026-11-17 after Recall → Edit).
 */
export function DepositPaEditForm({ pa }: { pa: ApiPa }) {
  const replaceTab = useReplaceTab(epmsRoutes)
  const queryClient = useQueryClient()
  useDocTabTitle(`Edit ${pa.pa_number}`)
  const updatePa = useUpdatePa()
  const [title, setTitle] = useState(pa.title)
  const [expectedRefund, setExpectedRefund] = useState(pa.expected_settlement_date ?? '')
  const [notes, setNotes] = useState(pa.notes ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const busy = updatePa.isPending || submitting

  if (!['draft', 'returned'].includes(pa.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-fg-muted">This PA cannot be edited.</p>
        <BackLink to={`/pa/${pa.id}`} className="mt-4"><Button variant="secondary">Back</Button></BackLink>
      </div>
    )
  }

  const save = async (andSubmit: boolean) => {
    if (busy || !title.trim()) return
    try {
      await updatePa.mutateAsync({
        id: pa.id,
        body: {
          title: title.trim(),
          // '' (not omitted) when cleared, so clearing sticks.
          notes: notes.trim(),
          expected_settlement_date: expectedRefund || undefined,
        },
      })
      if (andSubmit) {
        setSubmitting(true)
        setSubmitError(null)
        try {
          await paService.action(pa.id, { action: 'submit' })
        } catch (e) {
          setSubmitError(e instanceof Error ? e.message : 'Could not submit the PA')
          return
        } finally {
          setSubmitting(false)
        }
        void queryClient.invalidateQueries({ queryKey: ['pas'] })
        void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      }
      replaceTab(`/pa/${pa.id}`)
    } catch {
      // the refusal shows below the buttons from updatePa's error state
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-3">
        <IconButton label="Back to the PA" icon={<ArrowLeft className="h-5 w-5" />} size="icon" type="button"
          onClick={() => replaceTab(`/pa/${pa.id}`)} />
        <div>
          <h1 className="text-2xl font-semibold text-fg">Edit Deposit PA</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            {pa.pa_number} · {pa.status === 'returned' ? 'Returned — revise and resubmit' : 'Draft'}
          </p>
        </div>
      </div>

      <Alert tone="info">
        The amount is the agreement’s deposit and cannot be changed here. To change it, edit the
        Deposit Amount on the agreement (while it is a draft), or cancel this PA and raise a new one.
      </Alert>

      <Card className="flex flex-col gap-4 p-5">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 text-sm">
          <div>
            <dt className="text-xs font-medium text-fg-muted">Purchase Agreement</dt>
            <dd className="mt-0.5">
              {pa.agreement_id
                ? <Link to={`/agreements/${pa.agreement_id}`} className="font-mono text-accent-fg hover:underline">{pa.agreement_number}</Link>
                : pa.agreement_number}
              <span className="text-fg-muted"> · {pa.vendor_name}</span>
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-fg-muted">Deposit Amount</dt>
            <dd className="mt-0.5 text-lg font-semibold text-fg">
              {formatAmount(Number(pa.payment_amount), pa.currency)}
            </dd>
          </div>
        </dl>
        <FormField label="Title" required htmlFor="depositEditTitle">
          <Input id="depositEditTitle" value={title} maxLength={255}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} />
        </FormField>
        <FormField label="Expected Refund Date" htmlFor="depositEditRefund">
          <Input id="depositEditRefund" type="date" value={expectedRefund}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setExpectedRefund(e.target.value)} />
        </FormField>
        <FormField label="Notes" htmlFor="depositEditNotes">
          <Textarea id="depositEditNotes" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} className="resize-none" />
        </FormField>
      </Card>

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => replaceTab(`/pa/${pa.id}`)} disabled={busy}>Cancel</Button>
        <Button variant="secondary" onClick={() => save(false)} disabled={busy || !title.trim()}>
          {updatePa.isPending && !submitting ? 'Saving…' : 'Save Draft'}
        </Button>
        <Button onClick={() => save(true)} disabled={busy || !title.trim()}>
          {submitting ? 'Submitting…' : 'Save & Submit'}
        </Button>
      </div>
      {updatePa.isError && (
        <p role="alert" className="text-right text-sm text-danger-fg">
          {updatePa.error instanceof Error && updatePa.error.message
            ? updatePa.error.message : 'Failed to update payment application'}
        </p>
      )}
      {submitError && (
        <p role="alert" className="text-right text-sm text-danger-fg">
          Saved as a draft but not submitted: {submitError}
        </p>
      )}
    </div>
  )
}
