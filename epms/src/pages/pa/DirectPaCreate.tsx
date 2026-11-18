import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2, CreditCard, AlertTriangle, ArrowLeft, Upload } from 'lucide-react'
import { BackLink } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { BudgetAccountCascade } from '@/components/budget/BudgetAccountCascade'
import { DirectPaBudgetPanel } from '@/components/pa/DirectPaBudgetPanel'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { useDepartments } from '@/hooks/useDepartments'
import { useCreateDirectPa, useDirectPaCandidates } from '@/hooks/usePas'
import { useStaticTabNav } from '@/hooks/useStaticTabNav'
import type { LinkedPoDefaults } from '@/services/pa'
import { SELECT_CLS, TH_CLS, cents, errorMessage, num } from '@/pages/freight/freightUi'

import { Checkbox, Select, TBody, THead, Table, Td, Textarea, Th, Tr } from '@uniops/shell'
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
        <BackLink to="/pa" className="p-2 rounded-lg hover:bg-surface-sunken text-fg-muted" aria-label="Back to Payment Applications">
          <ArrowLeft className="h-5 w-5" />
        </BackLink>
        <div>
          <h1 className="text-2xl font-semibold text-fg">Create Direct PA</h1>
          <p className="text-sm text-fg-muted mt-0.5">Pay vendor invoices that have no purchase order or agreement behind them</p>
        </div>
      </div>
      {perms.isLoading ? (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-fg-faint" /></div>
      ) : canCreate ? (
        <DirectPaForm preselectInvoiceId={preselectInvoiceId} />
      ) : (
        <div className="rounded-xl border border-line bg-surface py-16 text-center">
          <p className="text-sm font-medium text-fg-muted">You do not have access to create payment applications</p>
          <p className="text-xs text-fg-muted mt-1">Raising a Direct PA needs the Create / Edit PAs permission.</p>
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
  // Upload Invoice → the Invoices page's upload form in Direct PA mode
  // (?upload=direct): what is saved there is matched for a Direct PA and comes
  // back here ticked. Same gate as that page's own Upload Invoice button.
  const perms = useRolePermissions().data?.permissions
  const canUpload = user?.role === 'system_admin' || !!perms?.invoice_upload
  const { openTab } = useStaticTabNav()
  const uploadInvoice = () => openTab('/invoices', 'upload=direct')

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [departmentId, setDepartmentId] = useState<string>(user?.department_id ?? '')
  const [costCenterId, setCostCenterId] = useState<string | undefined>(undefined)
  const [budgetCode, setBudgetCode] = useState('')
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Arriving from an invoice's "Create Direct PA" button, or back from Upload
  // Invoice (several ids, comma-separated, when a PDF was split): tick them.
  // Keyed on the ids so a second shortcut into the same (kept-alive) tab
  // applies too, and on the offered list so ticks land once it has refetched.
  const offeredKey = ready.map((i) => i.id).join(',')
  useEffect(() => {
    if (!preselectInvoiceId) return
    const offered = new Set(ready.map((i) => i.id))
    const ids = preselectInvoiceId.split(',').filter((id) => offered.has(id))
    if (ids.length > 0) setSelected(new Set(ids))
  }, [preselectInvoiceId, offeredKey]) // eslint-disable-line react-hooks/exhaustive-deps

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

  // An invoice linked to a PO (Link to PO, not Match to PO) defaults the form
  // to that PO's department, cost center and budget code (user, 2026-10-30) —
  // when every ticked invoice that has one agrees. Applied once per new set of
  // defaults, so the fields stay editable; the cascade is remounted so its
  // department-change reset does not wipe the cost center set alongside.
  const linked: LinkedPoDefaults | null = (() => {
    const seen = new Map<string, LinkedPoDefaults>()
    for (const i of chosen) {
      const d = i.linked_po
      if (d) seen.set(`${d.department_id}|${d.cost_center_id}|${d.budget_code}`, d)
    }
    return seen.size === 1 ? [...seen.values()][0] : null
  })()
  const linkedKey = linked ? `${linked.department_id}|${linked.cost_center_id}|${linked.budget_code}` : ''
  const appliedKey = useRef('')
  const [cascadeKey, setCascadeKey] = useState(0)
  useEffect(() => {
    if (!linked || linkedKey === appliedKey.current) return
    appliedKey.current = linkedKey
    if (linked.department_id) {
      setDepartmentId(linked.department_id)
      setCostCenterId(linked.cost_center_id ?? undefined)
    }
    if (linked.budget_code) setBudgetCode(linked.budget_code)
    setCascadeKey((k) => k + 1)
  }, [linkedKey]) // eslint-disable-line react-hooks/exhaustive-deps

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
      <div className="lg:col-span-2 rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
          <span className="text-sm font-semibold text-fg-secondary">Invoices for a Direct PA</span>
          <span className="text-sm text-fg-muted">
            {isPending ? 'Loading…' : `${ready.length} ready to pay · invoices of one vendor go on one PA`}
          </span>
          {canUpload && (
            <Button variant="secondary" size="sm" onClick={uploadInvoice} className="ml-auto gap-1.5">
              <Upload className="h-3.5 w-3.5" />
              Upload Invoice
            </Button>
          )}
        </div>
        {isPending ? (
          <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-fg-faint" /></div>
        ) : ready.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-sm font-medium text-fg-muted">Nothing ready to pay</p>
            <p className="text-xs text-fg-muted mt-1">
              {canUpload
                ? 'Upload the invoice here — it is matched for a Direct PA and comes back ticked.'
                : <>An invoice is uploaded on the Invoices page, then matched with <span className="font-medium">Match → Direct PA</span>.</>}
            </p>
            {canUpload && (
              <Button size="sm" onClick={uploadInvoice} className="mt-4 gap-1.5">
                <Upload className="h-3.5 w-3.5" />
                Upload Invoice
              </Button>
            )}
          </div>
        ) : (
          <Table density="compact">
            <THead>
              <Tr className="bg-surface-subtle">
                <Th className={cn(TH_CLS, 'w-10')} />
                {['Invoice #', 'Vendor', 'Invoice Date', 'Due Date', 'Amount', 'Tax', 'Total'].map((h) => (
                  <Th key={h} className={cn(TH_CLS, ['Amount', 'Tax', 'Total'].includes(h) && 'text-right')}>{h}</Th>
                ))}
              </Tr>
            </THead>
            <TBody>
              {ready.map((inv) => {
                const due = dueInfo(inv.due_date, inv.status)
                const urgent = due.tone === 'overdue' || due.tone === 'soon'
                // Once one is ticked, only the same vendor and currency can join it.
                const blocked = !!vendorId && !selected.has(inv.id)
                  && (inv.vendor_id !== vendorId || inv.currency !== currency)
                return (
                  <Tr key={inv.id}
                    className={cn('border-b border-line-subtle', blocked ? 'opacity-40' : 'hover:bg-surface-subtle cursor-pointer')}
                    onClick={() => !blocked && toggle(inv.id)}>
                    <Td>
                      <Checkbox checked={selected.has(inv.id)} disabled={blocked}
                        onChange={() => toggle(inv.id)} onClick={(e) => e.stopPropagation()} />
                    </Td>
                    <Td>
                      <Link to={`/invoices/${inv.id}`} onClick={(e) => e.stopPropagation()} className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                        {inv.vendor_invoice_number}
                      </Link>
                      {inv.linked_po && (
                        <div className="text-2xs text-fg-muted">Linked to {inv.linked_po.po_number}</div>
                      )}
                    </Td>
                    <Td className="text-xs text-fg-secondary">{inv.vendor_name}</Td>
                    <Td className="text-xs text-fg-secondary">{formatDate(inv.invoice_date)}</Td>
                    <Td className={cn('px-3 py-2.5 text-xs', urgent ? 'font-semibold text-danger-fg' : 'text-fg-secondary')}>
                      {formatDate(inv.due_date)}{due.label && <span className="ml-1 text-2xs">({due.label})</span>}
                    </Td>
                    <Td align="right" className="font-mono text-xs">{formatAmount(num(inv.amount), inv.currency)}</Td>
                    <Td align="right" className="font-mono text-xs">{formatAmount(num(inv.tax_amount), inv.currency)}</Td>
                    <Td align="right" className="font-mono text-xs font-semibold">{formatAmount(num(inv.total_amount), inv.currency)}</Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <div className="rounded-xl border border-line bg-surface p-5 shadow-sm flex flex-col gap-3">
          <h3 className="text-xs font-semibold text-fg-muted">Charged To</h3>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Department *</label>
            <Select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} className={cn(SELECT_CLS, 'w-full')}>
              <option value="">Select department…</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Cost Center & Budget Account *</label>
            <BudgetAccountCascade
              key={cascadeKey}
              departmentId={departmentId || undefined}
              costCenterId={costCenterId}
              budgetCode={budgetCode}
              onChange={(next) => { setCostCenterId(next.costCenterId); setBudgetCode(next.budgetCode) }}
            />
            {!departmentId && (
              <p className="text-xs text-fg-muted">Select a department first to enable the budget account picker.</p>
            )}
            {linked && appliedKey.current === linkedKey && (
              <p className="text-xs text-fg-muted">
                Defaulted from linked PO <span className="font-mono">{linked.po_number}</span> — change if needed.
              </p>
            )}
          </div>
          <DirectPaBudgetPanel costCenterId={costCenterId} budgetCode={budgetCode} amount={subtotal} currency={currency} />
        </div>

        <div className="rounded-xl border border-line bg-surface p-5 shadow-sm flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Reason for payment *</label>
            <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this paid without a purchase order or agreement?"
              className="w-full resize-none" />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Title</label>
            <Input value={title} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} placeholder="Default: Direct Payment — vendor + invoice numbers" />
          </div>

          {chosen.length > 0 && (
            <div className="rounded-lg bg-surface-subtle px-3 py-2 text-xs space-y-1">
              <div className="flex justify-between"><span className="text-fg-muted">Subtotal</span><span className="font-mono">{formatAmount(subtotal, currency)}</span></div>
              <div className="flex justify-between"><span className="text-fg-muted">Tax</span><span className="font-mono">{formatAmount(taxTotal, currency)}</span></div>
              <div className="flex justify-between border-t border-line pt-1 font-semibold text-primary-800">
                <span>Payment amount</span><span className="font-mono">{formatAmount(cents(subtotal + taxTotal), currency)}</span>
              </div>
            </div>
          )}
          {mixed && (
            <p className="flex items-center gap-1 text-xs text-danger-fg"><AlertTriangle className="h-3.5 w-3.5" /> The ticked invoices are from different vendors or currencies.</p>
          )}
          {error && <p className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">{error}</p>}
          <Button onClick={submit} disabled={createPa.isPending || missing.length > 0 || mixed} className="gap-2">
            <CreditCard className="h-4 w-4" />
            {createPa.isPending ? 'Creating…' : 'Create Direct PA'}
          </Button>
          {missing.length > 0 && !createPa.isPending && (
            <p className="text-2xs text-fg-muted">Still needed: {missing.join(', ')}.</p>
          )}
          <p className="text-2xs text-fg-muted">
            The PA opens as a draft. Check it, then press Submit for Approval on the PA page — it goes through the
            PA (Direct) approval workflow.
          </p>
        </div>
      </div>
    </div>
  )
}
