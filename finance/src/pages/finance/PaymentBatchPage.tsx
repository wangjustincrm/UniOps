/**
 * Payment Batch workbench (Phase a A4 UI)
 *
 * CRM pays most invoices in batches ("攒一起一起付"). Flow:
 *   1. Due list — approved PAs awaiting payment (single currency per run)
 *   2. Select rows → create a draft batch (snapshot)
 *   3. Execute — each line runs through the unified payment executor with
 *      per-line failure isolation; see paid / failed per line
 *
 * Styling follows the Portal convention (CoaConfigPage): neutral palette,
 * zebra rows, status pills, #085E5E primary.
 */
import { useEffect, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Play, Wallet, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { RemittancePanel } from '@/components/remittance/RemittancePanel'
import { RemittanceDialog } from '@/components/remittance/RemittanceDialog'
import { fetchPreview, scopeKey } from '@/services/remittance'
import { suggestCredits, type CreditSuggestResponse } from '@/services/vendorCredits'

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

interface Due {
  doc_kind: string; doc_id: string; doc_number: string | null
  payee: string; vendor_inv_no?: string; amount: string; currency: string
}
interface Batch {
  id: string; batch_number: string; batch_date: string; status: string
  currency: string; total: string; payment_method: string
  bank_account_id: string | null
}
interface BankAccount { id: string; name: string; bank_name: string; currency: string; account_masked: string | null }
interface BatchLine {
  id: string; doc_kind: string; doc_id: string; doc_number: string | null
  vendor_inv_no?: string; amount: string; status: string; error: string | null
}

function fmtMoney(v: string, ccy?: string): string {
  const s = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: 'bg-neutral-100 text-neutral-600', executed: 'bg-green-50 text-green-700',
    cancelled: 'bg-neutral-100 text-neutral-400',
    paid: 'bg-green-50 text-green-700', failed: 'bg-red-50 text-red-700',
    pending: 'bg-amber-50 text-amber-700',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-neutral-100 text-neutral-600')}>{status}</span>
}

export default function PaymentBatchPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [currency, setCurrency] = useState('CAD')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [openBatch, setOpenBatch] = useState<string | null>(null)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // Gate the Create/Execute controls on actual payment authority (ap_clerk /
  // finance roles or a finance_bp / finance_manager assignment) — the same gate
  // the create_batch / execute_batch endpoints enforce. NOT COA-manage.
  const { data: payPerm } = useQuery({
    queryKey: ['payments-can-pay'],
    queryFn: () => financeApi.get<{ can_pay: boolean }>('/payments/can-pay'),
  })
  const canPay = payPerm?.can_pay ?? false

  const { data: due = [] } = useQuery({
    queryKey: ['payments-due', currency],
    queryFn: () => financeApi.get<Due[]>(`/payments/due?currency=${currency}`),
  })
  const { data: batches = [] } = useQuery({
    queryKey: ['payment-batches'],
    queryFn: () => financeApi.get<Batch[]>('/payments/batches'),
  })

  const flash = (kind: 'ok' | 'err', text: string) => {
    setBanner({ kind, text }); setTimeout(() => setBanner(null), 6000)
  }

  const selectedTotal = useMemo(
    () => due.filter((d) => selected.has(d.doc_id)).reduce((s, d) => s + Number(d.amount), 0),
    [due, selected])

  const createBatch = useMutation({
    mutationFn: () => financeApi.post<Batch>('/payments/batches',
      { docs: [...selected].map((id) => {
        const d = due.find((x) => x.doc_id === id)!
        return { doc_kind: d.doc_kind, doc_id: id }
      }), payment_method: 'bank_transfer' }),
    onSuccess: (b) => {
      flash('ok', `Batch ${b.batch_number} created (${selected.size} items)`)
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['payment-batches'] })
      qc.invalidateQueries({ queryKey: ['payments-due'] })
      setOpenBatch(b.id)
    },
    onError: (e: Error) => flash('err', e.message),
  })

  if (!user) return <Navigate to="/login" replace />

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const allChecked = due.length > 0 && due.every((d) => selected.has(d.doc_id))

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/payment-batches"
      title="Payment Batches"
      subtitle="Group approved payments into a run and execute them through the unified payment flow"
    >
      <div className="mx-auto max-w-6xl">
        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {/* due list */}
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <Wallet className="h-5 w-5 text-neutral-400" />
          <span className="text-sm font-semibold text-neutral-700">Due for payment</span>
          <select value={currency} onChange={(e) => { setCurrency(e.target.value); setSelected(new Set()) }}
                  className={cn(inputCls, 'w-28')}>
            {['CAD', 'USD', 'CNY', 'EUR'].map((c) => <option key={c}>{c}</option>)}
          </select>
          <div className="ml-auto flex items-center gap-3">
            {selected.size > 0 && (
              <span className="text-sm text-neutral-600">
                {selected.size} selected · <span className="font-mono">{fmtMoney(String(selectedTotal), currency)}</span>
              </span>
            )}
            {canPay && (
              <button onClick={() => createBatch.mutate()} disabled={selected.size === 0 || createBatch.isPending}
                      className={primaryBtn}>
                {createBatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
                Create Batch
              </button>
            )}
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-2 w-10">
                  <input type="checkbox" checked={allChecked}
                         onChange={(e) => setSelected(e.target.checked ? new Set(due.map((d) => d.doc_id)) : new Set())} />
                </th>
                <th className="px-3 py-2 w-40">Document</th>
                <th className="px-3 py-2">Payee</th>
                <th className="px-3 py-2 w-44">Vendor Inv No</th>
                <th className="px-3 py-2 w-24">Type</th>
                <th className="px-3 py-2 w-40 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {due.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                  No approved {currency} payments awaiting a run.</td></tr>
              )}
              {due.map((d, i) => (
                <tr key={d.doc_id} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                  <td className="px-3 py-2">
                    <input type="checkbox" checked={selected.has(d.doc_id)} onChange={() => toggle(d.doc_id)} />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{d.doc_number || d.doc_id.slice(0, 8)}</td>
                  <td className="px-3 py-2">{d.payee}</td>
                  <td className="px-3 py-2 font-mono text-xs text-neutral-600">{d.vendor_inv_no || <span className="text-neutral-300">—</span>}</td>
                  <td className="px-3 py-2 text-xs text-neutral-500">{d.doc_kind === 'expense_claim' ? 'Claim' : d.doc_kind === 'pa_dir' ? 'Direct PA' : 'PA'}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmtMoney(d.amount, d.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* batches */}
        <h3 className="mb-2 mt-8 text-sm font-semibold text-neutral-700">Batches</h3>
        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-2 w-44">Batch</th>
                <th className="px-3 py-2 w-28">Date</th>
                <th className="px-3 py-2 w-24">Status</th>
                <th className="px-3 py-2">Method</th>
                <th className="px-3 py-2 w-40 text-right">Total</th>
                <th className="px-3 py-2 w-24" />
              </tr>
            </thead>
            <tbody>
              {batches.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">No batches yet.</td></tr>
              )}
              {batches.map((b, i) => (
                <tr key={b.id} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                  <td className="px-3 py-2 font-mono text-xs">{b.batch_number}</td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{b.batch_date}</td>
                  <td className="px-3 py-2"><StatusPill status={b.status} /></td>
                  <td className="px-3 py-2 text-xs text-neutral-500">{b.payment_method}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmtMoney(b.total, b.currency)}</td>
                  <td className="px-3 py-2 text-right">
                    <button onClick={() => setOpenBatch(b.id)} className={secondaryBtn}>Open</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {openBatch && (
        <BatchDetailModal batchId={openBatch} canPay={canPay}
          onClose={() => setOpenBatch(null)}
          onExecuted={(paid, failed) => {
            flash(failed ? 'err' : 'ok', `Executed: ${paid} paid${failed ? `, ${failed} failed` : ''}`)
            qc.invalidateQueries({ queryKey: ['payment-batches'] })
            qc.invalidateQueries({ queryKey: ['payments-due'] })
          }}
          onError={(m) => flash('err', m)} />
      )}
    </PortalChromeLayout>
  )
}

function BatchDetailModal({ batchId, canPay, onClose, onExecuted, onError }: {
  batchId: string; canPay: boolean; onClose: () => void
  onExecuted: (paid: number, failed: number) => void; onError: (m: string) => void
}) {
  const qc = useQueryClient()
  const [bankId, setBankId] = useState('')
  // Opened right after a successful execute so the operator can send
  // remittance advice without leaving the flow — but only when there is
  // someone to notify (see `execute`'s onSuccess below). Not opened just
  // because the batch happens to already be `executed` on load: that would
  // pop a modal in the operator's face every time they reopen a past batch
  // to check on it — the Remittance section below covers that case instead.
  const [showRemittance, setShowRemittance] = useState(false)
  // Vendor-credit netting preview, keyed by doc_id — fetched read-only while
  // the batch still sits in `draft` so the operator can see what the FIFO
  // default would apply before committing to Execute. `deselected` holds the
  // credit_ids the operator has unchecked per doc; a doc absent here (or
  // present with an empty Set) means "leave the automatic default alone".
  const [suggestions, setSuggestions] = useState<Record<string, CreditSuggestResponse>>({})
  const [deselected, setDeselected] = useState<Record<string, Set<string>>>({})
  const { data, isLoading } = useQuery({
    queryKey: ['batch', batchId],
    queryFn: () => financeApi.get<{ batch: Batch; lines: BatchLine[] }>(`/payments/batches/${batchId}`),
  })
  const { data: accounts = [] } = useQuery({
    queryKey: ['bank-accounts'],
    queryFn: () => financeApi.get<BankAccount[]>('/bank/accounts'),
  })

  const batch = data?.batch
  const lines = data?.lines ?? []

  // Vendor credits only apply to vendor payments (pa / pa_dir) — the suggest
  // endpoint 422s for expense_claim, and expense-claim payments never take
  // credits (Phase A design). Fetch one suggestion per creditable line in
  // parallel, once, while the batch is still draft.
  useEffect(() => {
    if (batch?.status !== 'draft') return
    const creditable = lines.filter((ln) => ln.doc_kind === 'pa' || ln.doc_kind === 'pa_dir')
    if (creditable.length === 0) return
    let cancelled = false
    void Promise.all(
      creditable.map(async (ln) => [ln.doc_id, await suggestCredits(ln.doc_kind, ln.doc_id)] as const),
    ).then((entries) => {
      if (!cancelled) setSuggestions(Object.fromEntries(entries))
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.status, lines])

  const toggleCredit = (docId: string, creditId: string) => {
    setDeselected((prev) => {
      const off = new Set(prev[docId] ?? [])
      off.has(creditId) ? off.delete(creditId) : off.add(creditId)
      return { ...prev, [docId]: off }
    })
  }

  /** Sum of `apply` for a doc's suggested credits, minus anything the
   * operator has deselected. Money fields are Decimal-as-string — Number()
   * before every arithmetic op. */
  const appliedFor = (docId: string): number => {
    const s = suggestions[docId]
    if (!s) return 0
    const off = deselected[docId] ?? new Set<string>()
    return s.suggested
      .filter((c) => !off.has(c.credit_id))
      .reduce((sum, c) => sum + Number(c.apply), 0)
  }

  const execute = useMutation({
    mutationFn: () => {
      // Build the override map only for docs the operator actually touched.
      // A doc left untouched must stay absent from the map so the server
      // keeps applying its own automatic FIFO default — sending its full id
      // list here would work today but would silently diverge from the
      // server's own choice if a new credit landed between preview and
      // execute.
      const creditIdsByDoc: Record<string, string[]> = {}
      for (const ln of lines) {
        const off = deselected[ln.doc_id]
        if (!off || off.size === 0) continue
        const s = suggestions[ln.doc_id]
        creditIdsByDoc[ln.doc_id] = (s?.suggested ?? [])
          .filter((c) => !off.has(c.credit_id))
          .map((c) => c.credit_id)
      }
      return financeApi.post<{ batch: Batch; paid: number; failed: number; lines: BatchLine[] }>(
        `/payments/batches/${batchId}/execute`,
        { bank_account_id: bankId, credit_ids_by_doc: creditIdsByDoc })
    },
    onSuccess: async (r) => {
      qc.invalidateQueries({ queryKey: ['batch', batchId] })
      onExecuted(r.paid, r.failed)
      // Zero paid lines means nobody to notify — don't stack a remittance
      // dialog on top of a failure the operator is already looking at.
      if (r.paid > 0) {
        // Prefetch under the exact key/queryFn RemittancePanel uses for this
        // scope, so when the dialog mounts below it reads the already-warm
        // cache instead of re-fetching. Gate the auto-popup on `enabled`:
        // at a company with remittance switched off, every batch execute
        // would otherwise pop a modal whose entire content is "not
        // configured" — the Remittance section on the executed batch view
        // still shows that message for anyone who goes looking for it.
        try {
          // Key must match RemittancePanel's own `['remittance-preview',
          // scopeKey(scope)]` exactly (see RemittancePanel.tsx) — it moved
          // off a hand-built `['remittance-preview', scope.kind, scope.id]`
          // tuple when the 'selection' scope was added, since 'selection'
          // has no singular `.id`. Building the key by hand here would
          // silently drift from that and defeat this prefetch (RemittancePanel
          // would just refetch under its own key instead of reading this
          // warm cache entry).
          const preview = await qc.fetchQuery({
            queryKey: ['remittance-preview', scopeKey({ kind: 'batch', id: batchId })],
            queryFn: () => fetchPreview({ kind: 'batch', id: batchId }),
          })
          if (preview.enabled) setShowRemittance(true)
        } catch {
          // The payment run already succeeded — onExecuted above already
          // reported it — so a failure to *preview* remittance is a separate,
          // secondary problem and must not be swallowed into "don't open"
          // (that would silently drop the remittance step) nor read as the
          // payment itself having failed. Fail open: show the dialog anyway
          // so the operator sees RemittancePanel's own retryable error state.
          setShowRemittance(true)
        }
      }
    },
    onError: (e: Error) => onError(e.message),
  })

  const banksForCcy = accounts.filter((a) => !batch || a.currency === batch.currency)
  const bankLabel = (id: string | null) => {
    const a = accounts.find((x) => x.id === id)
    return a ? `${a.name}${a.account_masked ? ` · …${a.account_masked}` : ''}` : '—'
  }

  return (
    <>
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-800">
            {batch ? `Batch ${batch.batch_number}` : 'Batch'}
            {batch && <span className="ml-2"><StatusPill status={batch.status} /></span>}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            {batch && (
              <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm text-neutral-600">
                <span>Date: {batch.batch_date}</span>
                <span>Currency: {batch.currency}</span>
                <span>Total: <span className="font-mono">{fmtMoney(batch.total, batch.currency)}</span></span>
                <span>{lines.length} lines</span>
                {batch.bank_account_id && <span>Bank: {bankLabel(batch.bank_account_id)}</span>}
              </div>
            )}
            <div className="max-h-80 overflow-y-auto rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2">Document</th>
                    <th className="px-3 py-2 w-40">Vendor Inv No</th>
                    <th className="px-3 py-2 w-24">Type</th>
                    <th className="px-3 py-2 w-24">Status</th>
                    <th className="px-3 py-2 w-32 text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((ln) => (
                    <tr key={ln.id} className="border-t border-neutral-100">
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs">{ln.doc_number || ln.doc_id.slice(0, 8)}</span>
                        {ln.error && <div className="text-xs text-red-600">{ln.error}</div>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600">{ln.vendor_inv_no || <span className="text-neutral-300">—</span>}</td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {ln.doc_kind === 'expense_claim' ? 'Claim' : ln.doc_kind === 'pa_dir' ? 'Direct PA' : 'PA'}
                      </td>
                      <td className="px-3 py-2"><StatusPill status={ln.status} /></td>
                      <td className="px-3 py-2 text-right font-mono">{fmtMoney(ln.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {batch?.status === 'draft' && lines.some((ln) => (suggestions[ln.doc_id]?.suggested.length ?? 0) > 0) && (
              <div className="mt-4">
                <h3 className="mb-2 text-sm font-semibold text-neutral-700">Vendor credits to apply</h3>
                <div className="space-y-3 rounded-lg border border-neutral-200 p-3">
                  {lines
                    .filter((ln) => (suggestions[ln.doc_id]?.suggested.length ?? 0) > 0)
                    .map((ln) => {
                      const s = suggestions[ln.doc_id]!
                      const off = deselected[ln.doc_id] ?? new Set<string>()
                      const applied = appliedFor(ln.doc_id)
                      const net = Number(s.gross) - applied
                      return (
                        <div key={ln.doc_id} className="border-b border-neutral-100 pb-3 last:border-0 last:pb-0 last:pt-0">
                          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs">
                            <span className="font-mono text-neutral-700">{ln.doc_number || ln.doc_id.slice(0, 8)}</span>
                            <span className="text-neutral-600">
                              Gross <span className="font-mono">{fmtMoney(s.gross)}</span>
                              {'  ·  '}Credits <span className="font-mono">{fmtMoney(String(applied))}</span>
                              {'  ·  '}Net <span className="font-mono font-semibold text-neutral-800">{fmtMoney(String(net))}</span>
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                            {s.suggested.map((c) => (
                              <label key={c.credit_id} className="flex items-center gap-1.5 text-xs text-neutral-600">
                                <input type="checkbox" checked={!off.has(c.credit_id)}
                                       onChange={() => toggleCredit(ln.doc_id, c.credit_id)} />
                                <span className="font-mono">{c.credit_number}</span>
                                <span className="text-neutral-400">{c.credit_date}</span>
                                <span className="font-mono">{fmtMoney(c.apply)}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>
              </div>
            )}

            {batch?.status === 'executed' && (
              <div className="mt-6">
                <h3 className="mb-2 text-sm font-semibold text-neutral-700">Remittance</h3>
                <RemittancePanel scope={{ kind: 'batch', id: batchId }} />
              </div>
            )}

            <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
              {batch?.status === 'executed' ? (
                <span className="flex items-center gap-1.5 text-sm text-green-700">
                  <CheckCircle2 className="h-4 w-4" /> Executed
                </span>
              ) : canPay && batch?.status === 'draft' ? (
                <>
                  <label className="flex items-center gap-2 text-sm text-neutral-600">
                    <span>Pay from bank</span>
                    <select value={bankId} onChange={(e) => setBankId(e.target.value)}
                            className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
                      <option value="">Select a {batch?.currency} account…</option>
                      {banksForCcy.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.bank_name} — {a.name}{a.account_masked ? ` · …${a.account_masked}` : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button onClick={() => execute.mutate()} disabled={execute.isPending || !bankId}
                          title={!bankId ? 'Select a funding bank first' : undefined} className={primaryBtn}>
                    {execute.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    Execute Batch
                  </button>
                </>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>

    <RemittanceDialog
      open={showRemittance}
      onClose={() => setShowRemittance(false)}
      scope={{ kind: 'batch', id: batchId }}
    />
    </>
  )
}
