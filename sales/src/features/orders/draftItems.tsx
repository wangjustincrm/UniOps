import { orderMoved, readMoved } from '@/features/common/moved'
import { useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ConfirmAction, Skeleton, useRecord, type BarItem } from '@/platform-ui'
import { ManualOrderForm } from './ManualOrderForm'
import { cancelManual, fetchOrder, ordersClient, submitManual, type OrderRead } from './ordersApi'
import { OPERATE, SUBMIT_ERRORS } from './draftRules'

/** A manual Draft in its record view (FR-OB-19a), in the action bar: Submit and Edit draft its buttons, Cancel
 *  draft… in "⋯". Edit opens the same form as New in `panel` (above the tabs); `node`: the cancel confirmation. */
export function useDraftItems(o: OrderRead | undefined, close: () => void): { items: BarItem[]; node: ReactNode; panel: ReactNode } {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [reading, setReading] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const detail = useRecord(ordersClient, o?.id ?? 0, fetchOrder, { enabled: editing && !!o })
  if (!o || o.source_type !== 'MANUAL' || o.fulfillment_status !== 'Draft') return { items: [], node: null, panel: null }
  // Submitted or cancelled: the list reads again and the view closes.
  const closes = { invalidates: orderMoved(o.id), after: close }
  const panel = !editing ? null : !detail.data || reading ? <Skeleton className="h-40 w-full" /> : (
    <ManualOrderForm draft={detail.data} onCancel={() => setEditing(false)}
      onSaved={async () => {
        // Read again before anything can be pressed: it stands on the version (and the lines) this save made.
        setReading(true)
        await readMoved(qc, o.id)
        setEditing(false)
        setReading(false)
      }} />
  )
  const items: BarItem[] = editing ? [] : [
    { id: 'submit-draft', label: 'Submit', permission: OPERATE, primary: 10, errorMessages: SUBMIT_ERRORS, ...closes,
      onSelect: () => submitManual(o.id, o.version) },
    { id: 'edit-draft', label: 'Edit draft', permission: OPERATE, primary: 20, refreshes: false, onSelect: () => setEditing(true) },
    { id: 'cancel-draft', label: 'Cancel draft…', permission: OPERATE, tone: 'destructive', opensDialog: true, refreshes: false,
      hint: 'Kept as Cancelled; never sent', onSelect: () => setCancelling(true) },
  ]
  const node = (
    <ConfirmAction open={cancelling} onOpenChange={setCancelling} title="Cancel this draft?" destructive confirmLabel="Cancel draft"
      description="The draft is kept as Cancelled; it is never sent." {...closes} onConfirm={() => cancelManual(o.id, o.version)} />
  )
  return { items, node, panel }
}
