import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2, CreditCard, AlertTriangle, ArrowLeft } from 'lucide-react'
import { BackLink } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { BudgetAccountCascade } from '@/components/budget/BudgetAccountCascade'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useDepartments } from '@/hooks/useDepartments'
import { useCreateDirectPa, useDirectPaCandidates } from '@/hooks/usePas'
import { SELECT_CLS, TH_CLS, cents, errorMessage, num } from '@/pages/freight/freightUi'

/** Payment Applications → Create Direct PA (/pa/new?source=direct[&invoice=<id>]).
 *  Pays invoices matched to no PO and no agreement (Match → Direct PA on the
 *  invoice): one or more of one vendor and currency, charged to a department
 *  (the creator's by default), a cost center of it and a budget account, with
 *  the reason for paying without a PO. Approved on the "PA (Direct)" workflow.
 *  Gated like POST /pa/direct on epms.pa.write. */
export function DirectPaCreate({ preselectInvoiceId }: { preselectInvoiceId: string | null }) {
  const { user } = useAuthStore()
  const perms = useRolePermissions()
  const canCreate = user?.role === 'system_admin' || !!perms.data?.permissions?.['epms.pa.write']
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <BackLink to="/pa" className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500" aria-label="Back to Payment Applications">
          <ArrowLeft className="h-5 w-5" />
        </BackLink>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Create Direct PA</h1>
          <p className="text-sm text-neutral-500 mt-0.5">Pay vendor invoices that have no purchase order or agreement behind them</p>
        </div>
      </div>
      {perms.isLoading ? (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-300" /></div>
      ) : canCreate ? (
        <DirectPaForm preselectInvoiceId={preselectInvoiceId} />
      ) : (
        <div className="rounded-xl border border-neutral-200 bg-white py-16 text-center">
          <p className="text-sm font-medium text-neutral-500">You do not have access to create payment applications</p>
          <p className="text-xs text-neutral-400 mt-1">Raising a Direct PA needs the Create / Edit PAs permission.</p>
        </div>
      )}
    </div>
  )
}

function DirectPaForm({ preselectInvoiceId }: { preselectInvoiceId: string | null }) {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const { data, isPending } = useDirectPaCandidates()
  const ready = data?.items ?? []
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [departmentId, setDepartmentId] = useState<string>(user?.department_id ?? '')
  const [costCenterId, setCostCenterId] = useState<string | undefined>(undefined)
  const [budgetCode, setBudgetCode] = useState('')
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Arriving from an invoice's "Create Direct PA" button: tick it. Keyed on the
  // id so a second shortcut into the same (kept-alive) tab applies too.
  useEffect(() => {
    if (!preselectInvoiceId || !ready.some((i) => i.id === preselectInvoiceId)) return
    setSelected(new Set([preselectInvoiceId]))
  }, [preselectInvoiceId, ready.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Drop selections that are no longer offered (another user put them on a PA).
  const chosen = ready.filter((i) => selected.has(i.id))
  const vendorId = chosen[0]?.vendor_id
  const currency = chosen[0]?.currency ?? 'CAD'
  const subtotal = cents(chosen.reduce((s, i) => s + num(i.amount), 0))
  const taxTotal = cents(chosen.reduce((s, i) => s + num(i.tax_amount), 0))
  const mixed = useMemo(
    () => new Set(chosen.map((i) => `${i.vendor_id}|${i.currency}`)).size > 1,
    [chosen],
  )

  const createPa = useCreateDirectPa()

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const missing = [
    chosen.length === 0 && 'an invoice',
    !departmentId && 'a department',
    !costCenterId && 'a cost center',
    !budgetCode && 'a budget account',
    !reason.trim() && 'the reason',
  ].filter(Boolean) as string[]

  const submit = () => {
    if (createPa.isPending || missing.length > 0 || mixed || !costCenterId) return
    setError(null)
    createPa.mutate(
      {
        invoice_ids: chosen.map((i) => i.id), department_id: departmentId,
        cost_center_id: costCenterId, budget_account_code: budgetCode,
        title: title.trim() || null, notes: reason.trim(),
      },
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
          <span className="text-sm font-semibold text-neutral-700">Invoices for a Direct PA</span>
          <span className="text-sm text-neutral-400">
            {isPending ? 'Loading…' : `${ready.length} ready to pay · invoices of one vendor go on one PA`}
          </span>
        </div>
        {isPending ? (
          <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-300" /></div>
        ) : ready.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-sm font-medium text-neutral-500">Nothing ready to pay</p>
            <p className="text-xs text-neutral-400 mt-1">
              Upload the invoice on the Invoices page, then on its Match panel choose <span className="font-medium">Direct PA</span>.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50">
                <th className={cn(TH_CLS, 'w-10')} />
                {['Invoice #', 'Vendor', 'Invoice Date', 'Due Date', 'Amount', 'Tax', 'Total'].map((h) => (
                  <th key={h} className={cn(TH_CLS, ['Amount', 'Tax', 'Total'].includes(h) && 'text-right')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ready.map((inv) => {
                const due = dueInfo(inv.due_date, inv.status)
                const urgent = due.tone === 'overdue' || due.tone === 'soon'
                // Once one is ticked, only the same vendor and currency can join it.
                const blocked = !!vendorId && !selected.has(inv.id)
                  && (inv.vendor_id !== vendorId || inv.currency !== currency)
                return (
                  <tr key={inv.id}
                    className={cn('border-b border-neutral-100', blocked ? 'opacity-40' : 'hover:bg-neutral-50 cursor-pointer')}
                    onClick={() => !blocked && toggle(inv.id)}>
                    <td className="px-3 py-2.5">
                      <input type="checkbox" checked={selected.has(inv.id)} disabled={blocked}
                        onChange={() => toggle(inv.id)} onClick={(e) => e.stopPropagation()} />
                    </td>
                    <td className="px-3 py-2.5">
                      <Link to={`/invoices/${inv.id}`} onClick={(e) => e.stopPropagation()} className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                        {inv.vendor_invoice_number}
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-neutral-700">{inv.vendor_name}</td>
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
        <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-3">
          <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide">Charged To</h3>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Department *</label>
            <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} className={cn(SELECT_CLS, 'w-full')}>
              <option value="">Select department…</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Cost Center & Budget Account *</label>
            <BudgetAccountCascade
              departmentId={departmentId || undefined}
              costCenterId={costCenterId}
              budgetCode={budgetCode}
              onChange={(next) => { setCostCenterId(next.costCenterId); setBudgetCode(next.budgetCode) }}
            />
            {!departmentId && (
              <p className="text-xs text-neutral-400">Select a department first to enable the budget account picker.</p>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Reason for payment *</label>
            <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this paid without a purchase order or agreement?"
              className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Title</label>
            <Input value={title} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} placeholder="Default: Direct Payment — vendor + invoice numbers" />
          </div>

          {chosen.length > 0 && (
            <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs space-y-1">
              <div className="flex justify-between"><span className="text-neutral-500">Subtotal</span><span className="font-mono">{formatAmount(subtotal, currency)}</span></div>
              <div className="flex justify-between"><span className="text-neutral-500">Tax</span><span className="font-mono">{formatAmount(taxTotal, currency)}</span></div>
              <div className="flex justify-between border-t border-neutral-200 pt-1 font-semibold text-primary-800">
                <span>Payment amount</span><span className="font-mono">{formatAmount(cents(subtotal + taxTotal), currency)}</span>
              </div>
            </div>
          )}
          {mixed && (
            <p className="flex items-center gap-1 text-xs text-danger-600"><AlertTriangle className="h-3.5 w-3.5" /> The ticked invoices are from different vendors or currencies.</p>
          )}
          {error && <p className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-600">{error}</p>}
          <Button onClick={submit} disabled={createPa.isPending || missing.length > 0 || mixed} className="gap-2">
            <CreditCard className="h-4 w-4" />
            {createPa.isPending ? 'Creating…' : 'Create Direct PA'}
          </Button>
          {missing.length > 0 && !createPa.isPending && (
            <p className="text-[11px] text-neutral-400">Still needed: {missing.join(', ')}.</p>
          )}
          <p className="text-[11px] text-neutral-400">
            The PA opens as a draft. Check it, then press Submit for Approval on the PA page — it goes through the
            PA (Direct) approval workflow.
          </p>
        </div>
      </div>
    </div>
  )
}
