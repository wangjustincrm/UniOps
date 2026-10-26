/**
 * Link to raw-material PO — records which NC raw-material / packaging order,
 * and which of its arrivals, a freight or customs invoice belongs to.
 *
 * The invoice is still paid by a Direct PA, to the forwarder / broker; this is
 * only a record of where the cost came from. It never changes the invoice's
 * match, status or payment (epms-api app/crud/po_fee.py).
 *
 * Two shapes it has to fit:
 *   - a freight bill the carrier combined: the WHOLE invoice → several arrivals,
 *     possibly of several POs;
 *   - a customs invoice: one LINE per PO, each line → that PO's arrival.
 * Both are the same act — pick what part of the invoice (whole / a line), what
 * fee type, then tick arrivals — so there is one editor, not two modes.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Link2, Loader2, Pencil, Search, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatAmount, formatDate } from '@/lib/utils'
import { useFeeLinkCandidates, useInvoiceFeeLinks, useSaveInvoiceFeeLinks } from '@/hooks/usePoFee'
import { FEE_TYPE_LABEL, type FeeType } from '@/services/poFee'
import type { InvoiceLineItem } from '@/services/invoices'

interface DraftLink {
  fee_type: FeeType
  po_id: string
  po_number: string
  gr_id: string | null
  gr_number: string | null
  invoice_line_id: string | null
}

// Candidates per request; "Show more" adds another page. The server reports
// the full count, so the picker never cuts the list off without saying so.
const PAGE = 50

const keyOf = (l: DraftLink) => `${l.invoice_line_id ?? '*'}|${l.fee_type}|${l.po_id}|${l.gr_id ?? '-'}`

/** A first guess from the invoice's own wording; the user can change it. */
function guessFeeType(text: string): FeeType {
  return /custom|clearance|duty|broker|cbsa|pga/i.test(text) ? 'customs' : 'freight'
}

export function InvoiceFeeLinksPanel({
  invoiceId, matchRoute, lineItems, currency, canEdit,
}: {
  invoiceId: string
  matchRoute: string | null | undefined
  lineItems: InvoiceLineItem[]
  currency: string
  canEdit: boolean
}) {
  const { data: links, isLoading } = useInvoiceFeeLinks(invoiceId)
  const save = useSaveInvoiceFeeLinks(invoiceId)
  const linkable = matchRoute == null || matchRoute === 'direct'

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<DraftLink[]>([])
  const [applyTo, setApplyTo] = useState<string>('*')      // '*' = whole invoice, else a line id
  const [feeType, setFeeType] = useState<FeeType>('freight')
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(PAGE)

  useEffect(() => {
    const t = setTimeout(() => { setDebouncedQ(q.trim()); setLimit(PAGE) }, 250)
    return () => clearTimeout(t)
  }, [q])
  const { data: page, isFetching } = useFeeLinkCandidates(debouncedQ, limit, editing)
  const candidates = page?.items

  const lineById = useMemo(
    () => new Map(lineItems.filter((l) => l.id).map((l) => [l.id as string, l])), [lineItems])

  const startEditing = () => {
    setDraft((links ?? []).map((l) => ({
      fee_type: l.fee_type, po_id: l.po_id, po_number: l.po_number,
      gr_id: l.gr_id, gr_number: l.gr_number, invoice_line_id: l.invoice_line_id,
    })))
    setFeeType(guessFeeType(lineItems.map((l) => l.description).join(' ')))
    setApplyTo('*')
    setError(null)
    setEditing(true)
  }

  // Changing which part of the invoice is being linked re-guesses the type
  // from that line's own description (a combined invoice can carry both).
  const chooseApplyTo = (v: string) => {
    setApplyTo(v)
    const line = v === '*' ? null : lineById.get(v)
    setFeeType(guessFeeType(line ? line.description : lineItems.map((l) => l.description).join(' ')))
  }

  const current = (po_id: string, gr_id: string | null): DraftLink => ({
    fee_type: feeType, po_id, po_number: '', gr_id, gr_number: null,
    invoice_line_id: applyTo === '*' ? null : applyTo,
  })
  const isTicked = (po_id: string, gr_id: string | null) =>
    draft.some((d) => keyOf(d) === keyOf(current(po_id, gr_id)))
  const toggle = (po_id: string, po_number: string, gr_id: string | null, gr_number: string | null) => {
    const link = { ...current(po_id, gr_id), po_number, gr_number }
    setDraft((prev) => prev.some((d) => keyOf(d) === keyOf(link))
      ? prev.filter((d) => keyOf(d) !== keyOf(link))
      : [...prev, link])
  }

  const handleSave = async () => {
    if (save.isPending) return
    setError(null)
    try {
      await save.mutateAsync(draft.map((d) => ({
        fee_type: d.fee_type, po_id: d.po_id, gr_id: d.gr_id, invoice_line_id: d.invoice_line_id,
      })))
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the links')
    }
  }

  const lineLabel = (lineId: string | null) => {
    if (!lineId) return 'Whole invoice'
    const l = lineById.get(lineId)
    return l ? `${l.description} (${formatAmount(Number(l.line_total), currency)})` : 'A line no longer on the invoice'
  }

  // Nothing to show: not linkable here and nothing linked.
  if (isLoading) return null
  if (!linkable && !(links && links.length)) return null

  const shown: DraftLink[] = editing ? draft : (links ?? []).map((l) => ({
    fee_type: l.fee_type, po_id: l.po_id, po_number: l.po_number,
    gr_id: l.gr_id, gr_number: l.gr_number, invoice_line_id: l.invoice_line_id,
  }))

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-400">
          <Link2 className="h-3.5 w-3.5" />
          Link to raw-material PO
        </h3>
        {canEdit && !editing && (
          <Button variant="secondary" size="sm" onClick={startEditing} disabled={!linkable && !links?.length}>
            <Pencil className="h-3.5 w-3.5" />
            {links?.length ? 'Edit links' : 'Link'}
          </Button>
        )}
      </div>

      <p className="mb-3 text-xs text-neutral-500">
        For a freight or customs invoice that belongs to an NC raw-material / packaging order. Shows on that
        PO&apos;s Freight &amp; Customs panel. Payment is unchanged — this invoice is still paid by a Direct PA.
        {!linkable && ' This invoice is matched on another route, so links can only be removed here.'}
      </p>

      {/* Current (or draft) links */}
      {shown.length === 0 ? (
        <p className="text-sm text-neutral-400">Not linked to any PO.</p>
      ) : (
        <ul className="mb-3 flex flex-col gap-1.5">
          {shown.map((l) => (
            <li key={keyOf(l)} className="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-100 bg-neutral-50 px-3 py-1.5 text-sm">
              <span className="rounded bg-white px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-600 border border-neutral-200">
                {FEE_TYPE_LABEL[l.fee_type]}
              </span>
              <Link to={`/po/${l.po_id}`} className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                {l.po_number}
              </Link>
              <span className="text-xs text-neutral-500">
                {l.gr_number ? `arrival ${l.gr_number}` : 'no arrival chosen'}
              </span>
              <span className="text-xs text-neutral-400">· {lineLabel(l.invoice_line_id)}</span>
              {editing && (
                <button
                  type="button"
                  className="ml-auto text-neutral-400 hover:text-danger-600"
                  aria-label="Remove link"
                  onClick={() => setDraft((prev) => prev.filter((d) => keyOf(d) !== keyOf(l)))}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <div className="flex flex-col gap-3 border-t border-neutral-100 pt-3">
          {linkable && (
            <>
              <div className="grid gap-2 sm:grid-cols-[1fr_9rem]">
                <label className="flex flex-col gap-1 text-xs text-neutral-500">
                  Part of the invoice
                  <select
                    value={applyTo}
                    onChange={(e) => chooseApplyTo(e.target.value)}
                    className="h-9 rounded border border-neutral-300 bg-white px-2 text-sm text-neutral-900"
                  >
                    <option value="*">Whole invoice</option>
                    {lineItems.filter((l) => l.id).map((l) => (
                      <option key={l.id} value={l.id}>
                        Line: {l.description} ({formatAmount(Number(l.line_total), currency)})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-neutral-500">
                  Fee type
                  <select
                    value={feeType}
                    onChange={(e) => setFeeType(e.target.value as FeeType)}
                    className="h-9 rounded border border-neutral-300 bg-white px-2 text-sm text-neutral-900"
                  >
                    {(Object.keys(FEE_TYPE_LABEL) as FeeType[]).map((t) => (
                      <option key={t} value={t}>{FEE_TYPE_LABEL[t]}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                <Input
                  className="pl-9"
                  placeholder="Search raw-material POs by PO number, supplier or arrival number…"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
              </div>

              <div className="max-h-72 overflow-y-auto rounded-lg border border-neutral-200">
                {isFetching && !candidates ? (
                  <div className="flex justify-center py-6 text-neutral-400"><Loader2 className="h-4 w-4 animate-spin" /></div>
                ) : !candidates?.length ? (
                  <p className="px-3 py-4 text-sm text-neutral-400">No raw-material POs match.</p>
                ) : (
                  <ul className="divide-y divide-neutral-100">
                    {candidates.map((po) => (
                      <li key={po.id} className="px-3 py-2">
                        <div className="flex flex-wrap items-baseline gap-2 text-sm">
                          <span className="font-mono text-xs font-semibold text-neutral-800">{po.number}</span>
                          <span className="text-neutral-600">{po.vendor_name}</span>
                          {po.incoterms_code && <span className="text-xs text-neutral-400">{po.incoterms_code}</span>}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                          {po.arrivals.length === 0 ? (
                            <label className="flex items-center gap-1.5 text-xs text-neutral-600">
                              <input type="checkbox" checked={isTicked(po.id, null)}
                                     onChange={() => toggle(po.id, po.number, null, null)} />
                              Whole PO (no arrival yet)
                            </label>
                          ) : po.arrivals.map((a) => (
                            <label key={a.id} className="flex items-center gap-1.5 text-xs text-neutral-600">
                              <input type="checkbox" checked={isTicked(po.id, a.id)}
                                     onChange={() => toggle(po.id, po.number, a.id, a.number)} />
                              <span className="font-mono">{a.number}</span>
                              <span className="text-neutral-400">{formatDate(a.received_at)}</span>
                            </label>
                          ))}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {page && page.total > page.items.length && (
                  <div className="flex items-center justify-between gap-3 border-t border-neutral-100 bg-neutral-50 px-3 py-2 text-xs text-neutral-500">
                    <span>
                      Showing {page.items.length} of {page.total} — latest arrivals first. Search to find a specific PO.
                    </span>
                    <Button variant="secondary" size="sm" disabled={isFetching}
                            onClick={() => setLimit((n) => n + PAGE)}>
                      {isFetching ? 'Loading…' : 'Show more'}
                    </Button>
                  </div>
                )}
              </div>
              <p className="text-xs text-neutral-400">
                Ticks apply to the part of the invoice and fee type chosen above. A freight bill the carrier
                combined can tick several arrivals; for a customs invoice, pick each line in turn and tick its PO.
              </p>
            </>
          )}

          {error && <p className="text-sm text-danger-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setEditing(false)} disabled={save.isPending}>
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
            <Button size="sm" onClick={handleSave} disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save links'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
