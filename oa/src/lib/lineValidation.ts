// Per-line required-field check shared by the General Expense (EXP) and Travel
// Expense (TRV) forms, so both say the same thing about a missing field.
//
// EXP used to filter out any line missing a description, budget account or net
// amount and save the rest without a word, while the scanned receipt of the
// dropped line was still attached (ISS-0193). TRV said only "All lines must
// have a budget account" with no line named (ISS-0201). Now the save is blocked
// and each message names the line and the field, e.g.
//   "Line 2: select a Budget Account"
//   "Meals — Lunch, line 1: select a Budget Account"
// and the page outlines the missing fields in red.

export type LineField = 'description' | 'budget_account_id' | 'net_amount'

export interface CheckedLine {
  description: string
  budget_account_id: string
  net_amount: string
  _file?: File | null
}

const ASK: Record<LineField, string> = {
  description: 'enter a Description',
  budget_account_id: 'select a Budget Account',
  net_amount: 'enter the Net amount',
}

/**
 * A line nobody has started: no description, account, amount or receipt.
 * "Add line item" makes such lines, and the General Expense form opens with
 * one, so a blank line is left out of the save instead of being refused —
 * there is nothing on it to lose.
 */
export function isBlankLine(l: CheckedLine): boolean {
  return !l.description.trim() && !l.budget_account_id && !l.net_amount.trim() && !l._file
}

/** Which of `required` the line is missing. */
export function missingFields(l: CheckedLine, required: LineField[]): LineField[] {
  return required.filter((f) => {
    if (f === 'net_amount') return l.net_amount.trim() === '' || isNaN(parseFloat(l.net_amount))
    return !String(l[f] ?? '').trim()
  })
}

/** "Line 2: select a Budget Account and enter the Net amount" */
export function lineMessage(where: string, fields: LineField[]): string {
  const asks = fields.map((f) => ASK[f])
  const said = asks.length > 1 ? `${asks.slice(0, -1).join(', ')} and ${asks[asks.length - 1]}` : asks[0]
  return `${where}: ${said}`
}

/** Red outline for a field the last save attempt found missing. */
export const INVALID_FIELD = 'border-danger-400 bg-danger-50/40'
