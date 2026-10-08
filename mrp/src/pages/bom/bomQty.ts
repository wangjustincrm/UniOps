// Shared quantity formatting for the BOM Explorer (tree rows + the
// where-used list), extracted 2026-09-03 from two identical local copies in
// BomExplorerPage.tsx and BomTreeNode.tsx that had to agree and had no
// mechanism forcing them to.
//
// Why not a plain `maximumFractionDigits: 4` (what both copies did): BOM
// ratios span an enormous dynamic range, because `qty_per` is a whole-batch
// NC quantity divided by the batch size. A 3000 kg batch using 0.012 kg of
// beta-carotene normalizes to 0.000004 — which rounds to a bare `0` at 4
// decimal places and reads on screen as missing data, not as a legitimately
// tiny ratio. Meanwhile 1332.8835 needs no more than its 4 decimals.
//
// So: keep 4 decimals as the FLOOR, and grant a small number as many extra
// decimals as it needs to show `SIGNIFICANT_DIGITS` significant digits.
// This can only ever ADD digits to what the old formatter produced, never
// remove any — every number that displayed acceptably before is untouched
// (verified against the S0147 tree: 607.6674 / 1332.8835 / 2988.528 all
// render identically; only 0 -> 0.000004 changes).
//
// Deliberately NOT `Intl`'s own `maximumSignificantDigits`: used alone it
// would TRUNCATE the large end (1332.883488 -> 1332.88 at 6 sig digits),
// and mixing it with `maximumFractionDigits` needs `roundingPriority`
// ('morePrecision'), an ES2023 option this repo has no browser-support
// floor for. Computing the decimal count ourselves needs neither.

const SIGNIFICANT_DIGITS = 6
const MIN_FRACTION_DIGITS = 4
const INTL_MAX_FRACTION_DIGITS = 20 // Intl.NumberFormat's own hard ceiling

/** Decimal fields arrive from the API as strings (see bomApi.ts); `null`
 *  renders as an em dash, an unparsable value passes through verbatim
 *  rather than being silently shown as NaN. */
export function formatQty(raw: string | null): string {
  if (raw === null) return '—'
  const n = Number(raw)
  if (!Number.isFinite(n)) return raw
  if (n === 0) return '0'
  // Decimals needed for SIGNIFICANT_DIGITS significant digits: for a value
  // whose leading digit sits at 10^exp, that's (SIGNIFICANT_DIGITS-1-exp).
  const exp = Math.floor(Math.log10(Math.abs(n)))
  const digits = Math.min(
    Math.max(MIN_FRACTION_DIGITS, SIGNIFICANT_DIGITS - 1 - exp),
    INTL_MAX_FRACTION_DIGITS,
  )
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(n)
}

/** The NC-native pair for one tree row: this component's whole-batch
 *  quantity (NC's `BD_BOM_B.NITEMNUM`) over the parent BOM header's batch
 *  size (`HNPARENTNUM`) — rendered as `610 / 3000` so a planner can read a
 *  row straight off the NC BOM screen instead of multiplying the
 *  normalized per-unit ratio in their head. Returns null when the API
 *  reports neither (the root row, or a pre-migration-0018 row with no batch
 *  size to state the quantity against). */
export function formatNcBatchQty(
  qtyPerBatch: string | null,
  parentBatchOutputQty: string | null,
): string | null {
  if (qtyPerBatch === null) return null
  const qty = formatQty(qtyPerBatch)
  if (parentBatchOutputQty === null) return qty
  return `${qty} / ${formatQty(parentBatchOutputQty)}`
}
