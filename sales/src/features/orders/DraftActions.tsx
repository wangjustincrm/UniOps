import { orderMoved } from '@/features/common/moved'
import { ActionButton, Stack } from '@/platform-ui'
import { submitManual, type OrderRead } from './ordersApi'
import { OPERATE, SUBMIT_ERRORS } from './draftRules'

/** Submit from the list row (a Draft). */
export function SubmitDraft({ o }: { o: OrderRead }) {
  return (
    <Stack as="span" inline gap={1}>
      <ActionButton size="sm" variant="secondary" permission={OPERATE} errorMessages={SUBMIT_ERRORS} done={`${o.source_reference ?? `#${o.order_number}`} submitted`}
        invalidates={orderMoved(o.id)} onAction={() => submitManual(o.id, o.version)}>Submit</ActionButton>
    </Stack>
  )
}
