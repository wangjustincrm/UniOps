/**
 * Column sorting for the Payment Batches due list.
 *
 * Client-side: the due list is loaded whole for one currency, so sorting the
 * rows already on the page is exact. Click a header to sort ascending, click it
 * again to reverse; another header starts ascending.
 *
 * Empty values (no due date, no invoice number) sit at the bottom in BOTH
 * directions — otherwise sorting by Due Date puts a block of "—" on top, which
 * is the opposite of what someone looking for the oldest due item wants.
 */

export type DueSortKey =
  | 'document' | 'submitted' | 'payee' | 'invoice' | 'due' | 'type' | 'amount'
export type SortDir = 'asc' | 'desc'
export interface DueSort { key: DueSortKey; dir: SortDir }

export interface SortableDue {
  doc_kind: string
  doc_number: string | null
  doc_id: string
  payee: string
  vendor_inv_no?: string
  invoices?: { id: string; number: string }[]
  due_date?: string | null
  submitted_at: string | null
  claim_type: string | null
  // po | agreement | freight | direct — a Freight PA (EPMS → Freight) is a `pa`
  pa_source?: string | null
  amount: string
}

/** The label the Type column shows — sorting by it must follow what is on screen. */
export function dueTypeLabel(d: SortableDue): string {
  return d.doc_kind === 'expense_claim' ? `Claim · ${d.claim_type ?? ''}`
    : d.doc_kind === 'pa_dir' ? 'Direct PA' : d.pa_source === 'freight' ? 'Freight PA' : 'PA'
}

function valueOf(d: SortableDue, key: DueSortKey): string | number | null {
  switch (key) {
    case 'document': return d.doc_number || null
    case 'submitted': return d.submitted_at || null
    case 'payee': return d.payee || null
    // The first invoice shown in the cell, else the plain vendor number.
    case 'invoice': return d.invoices?.[0]?.number || d.vendor_inv_no || null
    case 'due': return d.due_date || null           // YYYY-MM-DD sorts as text
    case 'type': return dueTypeLabel(d)
    case 'amount': {
      const n = Number(d.amount)
      return Number.isFinite(n) ? n : null
    }
  }
}

// Numeric-aware and case-insensitive, so "508027" sorts before "2026000066"
// and "amazon" sits with "Amazon".
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function sortDue<T extends SortableDue>(rows: T[], sort: DueSort | null): T[] {
  if (!sort) return rows
  const sign = sort.dir === 'asc' ? 1 : -1
  return rows
    .map((row, i) => ({ row, i, v: valueOf(row, sort.key) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) {
        if (a.v === b.v) return a.i - b.i
        return a.v === null ? 1 : -1                // empties last, either way
      }
      const c = typeof a.v === 'number' && typeof b.v === 'number'
        ? a.v - b.v
        : collator.compare(String(a.v), String(b.v))
      return c !== 0 ? sign * c : a.i - b.i         // stable on ties
    })
    .map((x) => x.row)
}

/** Next state after a header click. */
export function nextSort(current: DueSort | null, key: DueSortKey): DueSort {
  return current?.key === key
    ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: 'asc' }
}
