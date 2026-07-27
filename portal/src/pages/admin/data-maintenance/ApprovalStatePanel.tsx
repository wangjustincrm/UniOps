import { useState } from 'react'
import { adminApi } from '@/services/adminApi'

interface Props {
  system: string
  entity: string
  recordId: string
  currentStep: number | null
}

const ROLES = ['dept_manager', 'finance_bp', 'finance_manager', 'gm', 'opm', 'director',
                'supervisor', 'ap_clerk', 'quality_manager']

export function ApprovalStatePanel({ system, entity, recordId, currentStep }: Props) {
  const [step, setStep] = useState(currentStep == null ? '' : String(currentStep))
  const [role, setRole] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = async () => {
    setBusy(true); setMsg('')
    try {
      const patch: Record<string, unknown> = {}
      if (step !== '') patch.approval_step_idx = Number(step)
      if (role !== '') patch.assigned_role = role
      const res = await adminApi.editApprovalState(system, entity, recordId, patch)
      setMsg(`Updated. Reassigned ${res.reassigned_open_tasks ?? 0} open task(s).`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <p className="mb-2 text-sm font-medium">Approval State (manual override)</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Current step
          <input value={step} onChange={(e) => setStep(e.target.value)}
            className="h-8 w-20 rounded border border-neutral-300 px-2 text-sm" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Reassign open tasks to role
          <select value={role} onChange={(e) => setRole(e.target.value)}
            className="h-8 rounded border border-neutral-300 px-2 text-sm">
            <option value="">— leave unchanged —</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button type="button" onClick={apply} disabled={busy}
          className="h-8 rounded bg-amber-600 px-3 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50">
          {busy ? 'Applying…' : 'Apply'}
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-neutral-700">{msg}</p>}
      <p className="mt-1 text-[11px] text-neutral-400">Does not re-run the engine or send notifications. Completed tasks are untouched.</p>
    </div>
  )
}
