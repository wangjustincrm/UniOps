import { useState, type ChangeEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useReplaceTab } from '@uniops/shell'
import { ArrowLeft, Info } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { BudgetAccountCascade } from '@/components/budget/BudgetAccountCascade'
import { cn, formatAmount } from '@/lib/utils'
import { useDepartments } from '@/hooks/useDepartments'
import { useUpdatePa } from '@/hooks/usePas'
import { paService, type ApiPa } from '@/services/pa'
import { SELECT_CLS } from '@/pages/freight/freightUi'

/** Direct PA: amounts and invoices come from the invoices it pays (epms-api
 *  rejects them on PATCH); what it is charged to, the reason and the title are
 *  editable while it is a draft or returned. */
export function DirectPaEditForm({ pa }: { pa: ApiPa }) {
  const replaceTab = useReplaceTab(epmsRoutes)
  const queryClient = useQueryClient()
  useDocTabTitle(`Edit ${pa.pa_number}`)
  const updatePa = useUpdatePa()
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active || d.id === pa.department_id)
  const [title, setTitle] = useState(pa.title)
  const [reason, setReason] = useState(pa.notes ?? '')
  const [departmentId, setDepartmentId] = useState(pa.department_id ?? '')
  const [costCenterId, setCostCenterId] = useState<string | undefined>(pa.cost_center_id ?? undefined)
  const [budgetCode, setBudgetCode] = useState(pa.budget_account_code ?? '')
  const [submitting, setSubmitting] = useState(false)
  const busy = updatePa.isPending || submitting
  const complete = !!title.trim() && !!reason.trim() && !!departmentId && !!costCenterId && !!budgetCode

  if (!['draft', 'returned'].includes(pa.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-neutral-500">This PA cannot be edited.</p>
        <BackLink to={`/pa/${pa.id}`} className="mt-4"><Button variant="secondary">Back</Button></BackLink>
      </div>
    )
  }

  const save = async (andSubmit: boolean) => {
    if (busy || !complete || !costCenterId) return
    try {
      await updatePa.mutateAsync({ id: pa.id, body: {
        title: title.trim(), notes: reason.trim(), department_id: departmentId,
        cost_center_id: costCenterId, budget_account_code: budgetCode,
      } })
      if (andSubmit) {
        setSubmitting(true)
        try {
          await paService.action(pa.id, { action: 'submit' })
        } catch (e) {
          alert(e instanceof Error ? e.message : 'Could not submit the PA')
          return
        } finally {
          setSubmitting(false)
        }
        await queryClient.invalidateQueries({ queryKey: ['pas'] })
        await queryClient.invalidateQueries({ queryKey: ['tasks'] })
      }
      replaceTab(`/pa/${pa.id}`)
    } catch {
      // useUpdatePa alerts the server error
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-3">
        <button onClick={() => replaceTab(`/pa/${pa.id}`)} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Direct PA</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            {pa.pa_number} · {pa.status === 'returned' ? 'Returned — revise and resubmit' : 'Draft'} · {formatAmount(Number(pa.payment_amount), pa.currency)}
          </p>
        </div>
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-primary-100 bg-primary-50 px-4 py-3 text-xs text-primary-800">
        <Info className="h-4 w-4 shrink-0 mt-px" />
        <span>
          Amounts and invoices come from the invoices this PA pays. To change them, cancel this PA, correct
          the invoice and raise a new Direct PA.
        </span>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Title *</label>
          <Input value={title} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} maxLength={255} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Reason for payment *</label>
          <textarea rows={4} value={reason} onChange={(e) => setReason(e.target.value)}
            className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </div>
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
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => replaceTab(`/pa/${pa.id}`)} disabled={busy}>Cancel</Button>
        <Button variant="secondary" onClick={() => save(false)} disabled={busy || !complete}>
          {updatePa.isPending && !submitting ? 'Saving…' : 'Save Draft'}
        </Button>
        <Button onClick={() => save(true)} disabled={busy || !complete}>
          {submitting ? 'Submitting…' : 'Save & Submit'}
        </Button>
      </div>
    </div>
  )
}
