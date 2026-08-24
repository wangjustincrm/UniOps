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
