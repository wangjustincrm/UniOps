import { Tooltip } from '@/platform-ui'

/** An order line's number as a person reads it (`lineNumbers`): a Shopify
 *  line's position, with Shopify's line-item id in a tooltip; an 850's PO line. */
export function LineNo({ lineNo, numbers }: { lineNo: number; numbers: Map<number, number> }) {
  const shown = numbers.get(lineNo) ?? lineNo
  return shown === lineNo ? <>{lineNo}</> : (
    <Tooltip tip={`Shopify line item ${lineNo}`}><span data-line-id={lineNo}>{shown}</span></Tooltip>
  )
}
