/**
 * Who and what the imported-PO detail form may edit.
 *
 * Two screens consult these rules — the Edit Detail button on PO Detail and the
 * form page itself — and they were previously two hand-written copies of the
 * same condition. One got widened and the other did not, so the button appeared
 * and led straight to "cannot be edited here". Typechecking cannot catch that;
 * a shared predicate can.
 *
 * These must stay in step with epms-api/app/api/v1/po.py::update_imported_details.
 */

/** The shape these rules actually read — anything PO-like satisfies it. */
export interface ImportedEditablePo {
  source?: string | null
  has_invoice?: boolean
}

/**
 * Whether this PO is the kind the imported-detail form edits.
 *
 * Source is the whole test: there is deliberately NO status condition. Every
 * field the form writes (Incoterms, buyer notes, delivery address, sample,
 * supplier item id, prepaid) is detail the ERP has no column for, and the sync
 * will not overwrite it once a buyer has touched the PO — so a later NC change
 * cannot collide with it, and a closed or still-in-approval order gets asked
 * about its Incoterms just as often as an open one.
 *
 * Permission is a separate question, checked by the caller that needs it: the
 * button is permission-gated, the page is not (it is reached through the button,
 * and the endpoint is the real authority either way).
 */
export function isImportedEditablePo(po: ImportedEditablePo | null | undefined): boolean {
  return po?.source === 'nc'
}

/**
 * Whether the tax control must be locked.
 *
 * Tax rate is the one field on this form that moves money: changing it
 * re-derives tax_amount and total from the same subtotal. Once ANY invoice
 * points at the PO, that silently moves the very figure the 3-way variance was
 * measured against — so the rate freezes while every other field stays open.
 *
 * Reads `has_invoice`, which only the PO DETAIL endpoint fills in.
 * `has_unpaid_invoice` is not a substitute: the list endpoint alone computes
 * that one, and it is always false on a detail response.
 */
export function isImportedTaxLocked(po: ImportedEditablePo | null | undefined): boolean {
  return !!po?.has_invoice
}

// ── buyer-added lines ────────────────────────────────────────────────────────

/** The subset of a PO line these rules read. */
export interface EditablePoLine {
  ncSourced: boolean
  qty: number
  unitPrice: number
}

/**
 * Whether NC put this line on the PO.
 *
 * A response that predates the `nc_sourced` field omits it. Reading a missing
 * flag as "NC's" would make every line read-only and the feature invisible;
 * reading it as "the buyer's" would offer to edit lines the ERP owns and let
 * the save fail at the endpoint. The second is louder and recoverable, but the
 * first is the one that silently does nothing — so an ABSENT flag is treated as
 * NC's only when the PO itself came from NC, which is the only place the
 * distinction exists at all.
 */
export function isNcSourced(
  line: { nc_sourced?: boolean },
  po: ImportedEditablePo | null | undefined,
): boolean {
  if (line.nc_sourced !== undefined) return line.nc_sourced
  return isImportedEditablePo(po)
}

/** What the buyer-added lines come to, pre-tax. Mirrors crud.po.manual_lines_total. */
export function manualLinesTotal(lines: EditablePoLine[]): number {
  return round2(
    lines.filter((l) => !l.ncSourced)
      .reduce((sum, l) => sum + lineTotalOf(l), 0),
  )
}

/** One line's pre-tax amount, rounded the way the server rounds it. */
export function lineTotalOf(line: { qty: number; unitPrice: number }): number {
  return round2(line.qty * line.unitPrice)
}

/**
 * The PO's subtotal as it would stand with these lines.
 *
 * `storedSubtotal` already contains whatever the manual lines came to when the
 * page loaded, so NC's own figure is what is left after taking that away. This
 * is the same reconstruction the endpoint does — the preview and the saved
 * value have to agree, or the buyer watches the number jump on save.
 */
export function subtotalWith(
  storedSubtotal: number,
  storedManualTotal: number,
  lines: EditablePoLine[],
): number {
  return round2(storedSubtotal - storedManualTotal + manualLinesTotal(lines))
}

/** Currency arithmetic in floats needs pinning down at each step, not at the end. */
function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100
}
