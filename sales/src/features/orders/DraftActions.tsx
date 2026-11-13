import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, Skeleton, queryKeys } from '@/platform-ui'
import { ManualOrderForm } from './ManualOrderForm'
import { cancelManual, fetchOrder, ordersClient, submitManual, type OrderRead } from './ordersApi'

const OPERATE = 'sales.order.operate'
// What a refused submit says (user 2026-11-13: a manual order needs its carrier before it goes).
const SUBMIT_ERRORS: Record<string, string> = {
  'order.carrier': 'Choose how it ships first: Edit draft → Ships by.',
  'order.carrier_express': 'Purolator Express is not offered: Edit draft → choose Purolator or LTL.',
  'order.lines_invalid': 'Fix every line first: Edit draft.',
}

/** A manual Draft in its detail sheet (FR-OB-19a): Submit, Edit (the same form
 *  as New, here in the sheet) or Cancel the draft. */
export function DraftActions({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const detail = useQuery({ queryKey: queryKeys.detail(ordersClient, o.id), queryFn: () => fetchOrder(o.id), enabled: editing })
  const done = async () => { await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) }); close() }
  const submit = useMutation({ mutationFn: () => submitManual(o.id, o.version), onSuccess: done })
  if (editing) {
    if (!detail.data) return <Skeleton className="h-40 w-full" />
    return (
      <ManualOrderForm draft={detail.data} onCancel={() => setEditing(false)}
        onSaved={async () => {
          setEditing(false)
          await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
          await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
        }} />
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <ActionButton size="sm" permission={OPERATE} errorMessages={SUBMIT_ERRORS} onAction={() => submit.mutateAsync()}>Submit</ActionButton>
        <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setEditing(true)}>Edit draft</ActionButton>
        <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setCancelling(true)}>Cancel draft</ActionButton>
      </div>
      <ConfirmAction open={cancelling} onOpenChange={setCancelling} title="Cancel this draft?" destructive confirmLabel="Cancel draft"
        description="The draft is kept as Cancelled; it is never sent." onConfirm={async () => { await cancelManual(o.id, o.version); await done() }} />
    </div>
  )
}

/** Submit from the list row (a Draft). */
export function SubmitDraft({ o }: { o: OrderRead }) {
  const qc = useQueryClient()
  const m = useMutation({ mutationFn: () => submitManual(o.id, o.version),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) }) })
  return (
    <span className="inline-flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" permission={OPERATE} errorMessages={SUBMIT_ERRORS} done={`${o.source_reference ?? `#${o.order_number}`} submitted`} onAction={() => m.mutateAsync()}>Submit</ActionButton>
    </span>
  )
}
