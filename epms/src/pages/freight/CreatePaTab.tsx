import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2, CreditCard, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import {
  useCreateFreightPa, useFreightCarriers, useFreightInvoice, useFreightInvoiceDetails, useFreightInvoices,
} from '@/hooks/useFreight'
import { SELECT_CLS, TH_CLS, cents, errorMessage, num } from './freightUi'

export function CreatePaTab({ preselectInvoiceId }: { preselectInvoiceId: string | null }) {
  const navigate = useNavigate()
  const { data: carriers = [] } = useFreightCarriers()
  const [carrierId, setCarrierId] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Arriving from an invoice's "Create PA" button: pick its carrier and tick it.
  // Keyed on the id so a second shortcut into the same (kept-alive) tab applies too.
  const { data: pre } = useFreightInvoice(preselectInvoiceId ?? '', !!preselectInvoiceId)
  useEffect(() => {
    if (!pre) return
    setCarrierId(pre.carrier_id)
    setSelected(pre.status === 'matched' && !pre.pa_id ? new Set([pre.id]) : new Set())
  }, [pre?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data, isPending } = useFreightInvoices(
    { status: 'matched', carrier_id: carrierId, page_size: 200 }, !!carrierId,
  )
  const ready = (data?.items ?? []).filter((i) => !i.pa_id)

  // Drop selections that are no longer offered (another user put them on a PA).
  const selectedIds = ready.filter((i) => selected.has(i.id)).map((i) => i.id)
  const details = useFreightInvoiceDetails(selectedIds)

  const split = useMemo(() => {
    const m = new Map<string, { code: string | null; name: string | null; dept: string | null; amount: number; tax: number }>()
    for (const inv of details.items) {
      for (const t of inv.totals_by_cost_center) {
        const k = t.cost_center_id ?? 'none'
        const cur = m.get(k) ?? { code: t.cost_center_code, name: t.cost_center_name, dept: t.department_name, amount: 0, tax: 0 }
        cur.amount = cents(cur.amount + num(t.amount))
        cur.tax = cents(cur.tax + num(t.tax_amount))
        m.set(k, cur)
      }
    }
    return [...m.values()].sort((a, b) => b.amount - a.amount)
  }, [details.items])

  const chosen = ready.filter((i) => selected.has(i.id))
  const currencies = new Set(chosen.map((i) => i.currency))
  const currency = chosen[0]?.currency ?? 'CAD'
  const subtotal = cents(chosen.reduce((s, i) => s + num(i.amount), 0))
  const taxTotal = cents(chosen.reduce((s, i) => s + num(i.tax_amount), 0))

  const createPa = useCreateFreightPa()

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const submit = () => {
    if (createPa.isPending || selectedIds.length === 0) return
    if (currencies.size > 1) return setError('All invoices on one PA must be in the same currency.')
    setError(null)
    createPa.mutate(
      { invoice_ids: selectedIds, title: title.trim() || null, notes: notes.trim() || null },
      {
        onSuccess: (pa) => navigate(`/pa/${pa.id}`),
        onError: (e) => setError(errorMessage(e, 'Could not create the PA')),
      },
    )
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="lg:col-span-2 rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-neutral-200 px-5 py-3">
          <select value={carrierId} onChange={(e) => { setCarrierId(e.target.value); setSelected(new Set()) }} className={SELECT_CLS}>
            <option value="">Select carrier…</option>
            {carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <span className="text-sm text-neutral-400">
            {carrierId ? (isPending ? 'Loading…' : `${ready.length} matched invoice${ready.length !== 1 ? 's' : ''} not on a PA`) : 'Matched invoices of one carrier go on one PA'}
          </span>
        </div>
        {!carrierId ? (
          <div className="py-16 text-center text-sm text-neutral-400">Choose a carrier to see its invoices ready for payment</div>
        ) : isPending ? (
          <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-300" /></div>
        ) : ready.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-sm font-medium text-neutral-500">Nothing ready to pay</p>
            <p className="text-xs text-neutral-400 mt-1">Finalise the matching on an invoice first (Invoices tab)</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50">
                <th className={cn(TH_CLS, 'w-10')}>
                  <input type="checkbox" aria-label="Select all"
                    checked={ready.length > 0 && ready.every((i) => selected.has(i.id))}
                    onChange={(e) => setSelected(e.target.checked ? new Set(ready.map((i) => i.id)) : new Set())} />
                </th>
                {['Invoice #', 'Invoice Date', 'Due Date', 'Amount', 'Tax', 'Total'].map((h) => (
                  <th key={h} className={cn(TH_CLS, ['Amount', 'Tax', 'Total'].includes(h) && 'text-right')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ready.map((inv) => {
                const due = dueInfo(inv.due_date, inv.status)
                const urgent = due.tone === 'overdue' || due.tone === 'soon'
                return (
                  <tr key={inv.id} className="border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer" onClick={() => toggle(inv.id)}>
                    <td className="px-3 py-2.5"><input type="checkbox" checked={selected.has(inv.id)} onChange={() => toggle(inv.id)} onClick={(e) => e.stopPropagation()} /></td>
                    <td className="px-3 py-2.5">
                      <Link to={`/freight/invoices/${inv.id}`} onClick={(e) => e.stopPropagation()} className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                        {inv.vendor_invoice_number}
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-neutral-600">{formatDate(inv.invoice_date)}</td>
                    <td className={cn('px-3 py-2.5 text-xs', urgent ? 'font-semibold text-danger-600' : 'text-neutral-600')}>
                      {formatDate(inv.due_date)}{due.label && <span className="ml-1 text-[10px]">({due.label})</span>}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(num(inv.amount), inv.currency)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(num(inv.tax_amount), inv.currency)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs font-semibold">{formatAmount(num(inv.total_amount), inv.currency)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
          <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-3">Cost Split Preview</h3>
          {selectedIds.length === 0 ? (
            <p className="text-xs text-neutral-400">Tick one or more invoices.</p>
          ) : details.isLoading ? (
            <div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-neutral-300" /></div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-neutral-500">
                  <th className="pb-1.5 text-left font-medium">Cost center</th>
                  <th className="pb-1.5 text-right font-medium">Amount</th>
                  <th className="pb-1.5 text-right font-medium">Tax</th>
                </tr>
              </thead>
              <tbody>
                {split.map((r, i) => (
                  <tr key={i} className="border-t border-neutral-100">
                    <td className="py-1.5">
                      <div className="text-neutral-800">{r.code ? `${r.code} — ${r.name ?? ''}` : '—'}</div>
                      <div className="text-[10px] text-neutral-400">{r.dept ?? ''}</div>
                    </td>
                    <td className="py-1.5 text-right font-mono">{formatAmount(r.amount, currency)}</td>
                    <td className="py-1.5 text-right font-mono">{formatAmount(r.tax, currency)}</td>
                  </tr>
                ))}
                <tr className="border-t-2 border-neutral-200 font-semibold">
                  <td className="py-1.5">Total</td>
                  <td className="py-1.5 text-right font-mono">{formatAmount(subtotal, currency)}</td>
                  <td className="py-1.5 text-right font-mono">{formatAmount(taxTotal, currency)}</td>
                </tr>
              </tbody>
            </table>
          )}
          {selectedIds.length > 0 && (
            <div className="mt-3 flex items-center justify-between rounded-lg bg-primary-50 px-3 py-2">
              <span className="text-xs font-semibold text-primary-700">Payment amount</span>
              <span className="font-mono text-sm font-bold text-primary-800">{formatAmount(cents(subtotal + taxTotal), currency)}</span>
            </div>
          )}
          {currencies.size > 1 && (
            <p className="mt-2 flex items-center gap-1 text-xs text-danger-600"><AlertTriangle className="h-3.5 w-3.5" /> The ticked invoices are in different currencies.</p>
          )}
        </div>

        <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Title</label>
            <Input value={title} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} placeholder="Default: Freight — carrier + invoice numbers" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Notes</label>
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
              className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
          </div>
          {error && <p className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-600">{error}</p>}
          <Button onClick={submit} disabled={createPa.isPending || selectedIds.length === 0 || currencies.size > 1} className="gap-2">
            <CreditCard className="h-4 w-4" />
            {createPa.isPending ? 'Creating…' : 'Create Freight PA'}
          </Button>
          <p className="text-[11px] text-neutral-400">The PA opens as a draft. Check it, then press Submit for Approval on the PA page.</p>
        </div>
      </div>
    </div>
  )
}
