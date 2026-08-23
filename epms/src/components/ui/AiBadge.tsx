// The "this value came from the document, not from you" marker.
//
// Lived as a private function inside InvoiceListPage until Task 14, which
// needed the identical marker on the agreement-receipt vendor picker. Moved
// here rather than copied: the badge is a PROMISE to the reader ("a machine
// filled this in — check it before you rely on it"), and a promise that is
// styled slightly differently on two pages reads as two different promises.
//
// The markup is byte-for-byte what InvoiceListPage rendered before the move —
// same element, same classes, same text — so that live page is pixel-identical
// after it.
export function AiBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-primary-50 border border-primary-200 px-1.5 py-0.5 text-[10px] font-medium text-primary-600 ml-1.5">
      AI
    </span>
  )
}
