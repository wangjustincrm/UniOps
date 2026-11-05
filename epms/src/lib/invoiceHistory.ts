import type { InvoiceHistoryEntry } from '@/services/invoices'

// Wording for one recorded step on the invoice History tab (ISS-0170). The
// keys read here are the ones epms-api/app/api/v1/invoices.py writes for each
// action (_audit_invoice / _audit_unmatch); anything missing degrades to a
// shorter sentence, never to "undefined".

export interface HistoryLine {
  action: string
  note?: string
  color: string
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)

/** "PO-400-2610-37 · GR-…", "Agreement AGR-…", or "a Direct PA". */
export function matchTargetLabel(t: Record<string, unknown> | null | undefined): string {
  if (!t) return 'a document'
  if (t.route === 'direct') return 'a Direct PA (no PO or agreement)'
  if (t.route === 'agreement' || (!str(t.po_number) && str(t.agreement_number))) {
    return `Agreement ${str(t.agreement_number) ?? ''}`.trim()
  }
  const po = str(t.po_number) ?? 'a PO'
  const gr = str(t.gr_number)
  return gr ? `${po} · ${gr}` : po
}

const STATUS_SUFFIX: Record<string, string> = {
  match_review: ' — sent to AP for review',
  exception: ' — exception raised',
  unmatched: ' — back to Unmatched',
}

export function historyLine(e: InvoiceHistoryEntry): HistoryLine {
  const a = e.after ?? {}
  const b = e.before ?? {}
  const status = str(a.status)
  const suffix = status ? STATUS_SUFFIX[status] ?? '' : ''
  const note = str(a.note) ?? undefined
  switch (e.action) {
    case 'assign_match':
      return {
        action: `${a.reassigned ? 'Reassigned' : 'Assigned'} matching to ${str(a.assignee_name) ?? 'a user'}`,
        color: 'bg-primary-600',
      }
    case 'decline_match':
      return { action: 'Declined the match assignment', note, color: 'bg-warning-500' }
    case 'match':
      return {
        action: `Matched to ${matchTargetLabel(a)}${suffix}`,
        color: status === 'exception' ? 'bg-danger-600' : 'bg-success-600',
      }
    case 'review_approve':
      return {
        action: `Match to ${matchTargetLabel(b)} confirmed by AP${suffix}`,
        note,
        color: status === 'exception' ? 'bg-danger-600' : 'bg-success-600',
      }
    case 'review_reject':
      return {
        action: `Match to ${matchTargetLabel(b)} rejected by AP — sent back to re-match`,
        note,
        color: 'bg-danger-600',
      }
    case 'resolve_exception':
      return {
        action: a.resolution === 'accepted' ? 'Exception accepted with justification' : 'Credit note requested',
        note,
        color: 'bg-warning-500',
      }
    case 'rematch_on_edit':
      return {
        action: `Edit re-ran the match — now ${status === 'matched' ? `matched to ${matchTargetLabel(a)}` : (status ?? 'changed')}`,
        color: status === 'exception' ? 'bg-danger-600' : 'bg-neutral-400',
      }
    case 'unmatch': {
      const reason = str(a.reason) ?? undefined
      if (a.scope === 'gr') {
        return { action: `Goods receipt ${str(b.gr_number) ?? ''} unlinked`.replace(/\s+/g, ' '), note: reason, color: 'bg-neutral-500' }
      }
      if (a.scope === 'direct') {
        return { action: 'Taken off the Direct PA route — back to Unmatched', note: reason, color: 'bg-neutral-500' }
      }
      return { action: `Unmatched from ${str(b.po_number) ?? 'its PO'} — back to Unmatched`, note: reason, color: 'bg-neutral-500' }
    }
    default:
      return { action: e.action, color: 'bg-neutral-400' }
  }
}

/** Recorded actions that stand for "the invoice was matched". An invoice with
 *  none of them was matched before steps were recorded, so the page falls back
 *  to the entry derived from its current fields. */
export const MATCH_ACTIONS = new Set(['match', 'review_approve', 'resolve_exception', 'rematch_on_edit'])
