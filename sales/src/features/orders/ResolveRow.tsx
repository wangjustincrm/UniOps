import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Autocomplete, ConfirmAction, FormSheet, Hint, queryKeys, useNotices, type ComboOption } from '@/platform-ui'
import { useTestRunRefusal } from '@/features/common/testRun'
import { ordersClient, resolveRow, type LineRead, type ShipmentRowRead } from './ordersApi'

const OPERATE = 'sales.order.operate'
const errorMessages: Record<string, string> = {
  'shipment.row_resolved': 'This row needs no decision any more: reload the order.',
  'order.line': 'That line is not open on this order.',
  'shipment.row_other_item': 'This row is another item than that line: accept it with a reason.',
  'auth.forbidden': 'You do not have permission to do this.',
}

/** A 945 row matching no order line: link it to a line of the same item (its quantity then counts
 *  there, and on the invoice), or accept it with a reason (a different or substituted item adds
 *  nothing). Which lines match is the server's. Once none is left, the invoice it blocked follows. */
export function ResolveRow({ r, shipmentId, orderId, lines }: { r: ShipmentRowRead; shipmentId: number; orderId: number; lines: LineRead[] }) {
  const qc = useQueryClient()
  const notices = useNotices()
  const refused = useTestRunRefusal()
  const [open, setOpen] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [line, setLine] = useState<string | null>(null)
  const m = useMutation({
    mutationFn: ({ lineId, why }: { lineId: number | null; why: string | null }) => resolveRow(shipmentId, r.id, lineId, why),
    onSuccess: async () => {
      notices.notify({ key: `row:${r.id}`, tone: 'success', text: `945 row ${r.item_code ?? ''} resolved.` })
      setOpen(false)
      await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, orderId) })
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
    },
  })
  const matching = lines.filter((l) => !l.removed && (r.matching_lines ?? []).includes(l.id))
  const options = async (q: string): Promise<ComboOption[]> => matching
    .map((l) => ({ value: String(l.id), label: `Line ${l.line_no} · ${l.item_code ?? '?'}`, hint: `${l.qty_ea ?? '?'} EA` }))
    .filter((o) => o.label.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <>
      <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setOpen(true)}>Resolve</ActionButton>
      <FormSheet open={open} onOpenChange={setOpen} title={`945 row ${r.item_code ?? ''}: ${r.qty_shipped_ea} EA`}
        description="It matches no line of the order, so the invoice waits for your decision.">
        <div className="flex flex-col gap-3">
          {matching.length > 0 ? (
            <>
              <label className="flex max-w-sm flex-col gap-1 text-sm">
                Order line
                <Autocomplete aria-label="Order line" value={line} onChange={setLine} loadOptions={options} />
              </label>
              <Hint as="p">Linked: its quantity counts on that line and on the invoice.</Hint>
              <div>
                <ActionButton size="sm" permission={OPERATE} disabled={!line} errorMessages={errorMessages}
                  onAction={() => m.mutateAsync({ lineId: Number(line), why: null })}>Link to this line</ActionButton>
              </div>
            </>
          ) : <Hint as="p">No line of this order is the same item, so it can only be accepted.</Hint>}
          <div className="flex flex-col items-start gap-1">
            <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setAccepting(true)}>Accept without a line</ActionButton>
            <Hint as="p">Accepted: it stays on the 945 and adds nothing to the line or the invoice.</Hint>
          </div>
        </div>
      </FormSheet>
      <ConfirmAction open={accepting} onOpenChange={setAccepting} title="Accept this row without a line?" confirmLabel="Accept"
        reason={{ label: 'Why it is accepted' }} errorMessages={errorMessages} onConfirm={(why) => m.mutateAsync({ lineId: null, why }).catch(refused)} />
    </>
  )
}
