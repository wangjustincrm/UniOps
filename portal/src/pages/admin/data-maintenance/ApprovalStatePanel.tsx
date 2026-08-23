import { useEffect, useState } from 'react'
import { adminApi, type WorkflowStep } from '@/services/adminApi'

interface Props {
  system: string
  entity: string
  recordId: string
  currentStep: number | null
}

export function ApprovalStatePanel({ system, entity, recordId, currentStep }: Props) {
  const [step, setStep] = useState(currentStep == null ? '' : String(currentStep))
  const [role, setRole] = useState('')
  const [msg, setMsg] = useState('')
  const [warn, setWarn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<WorkflowStep[]>([])
  const [openTasks, setOpenTasks] = useState<number | null>(null)
  const [chainError, setChainError] = useState('')

  // The document's real chain. Without it the step box is free text, which is exactly
  // how a document ends up parked on a step past the end of its workflow — the engine
  // then refuses to act on it and says nothing.
  useEffect(() => {
    let cancelled = false
    adminApi.workflowSteps(system, entity, recordId)
      .then((r) => {
        if (cancelled) return
        setSteps(r.steps)
        setOpenTasks(r.open_approve_tasks)
      })
      .catch((e) => { if (!cancelled) setChainError(e instanceof Error ? e.message : 'unavailable') })
    return () => { cancelled = true }
  }, [system, entity, recordId])

  const apply = async () => {
    setBusy(true); setMsg(''); setWarn(false)
    try {
      const patch: Record<string, unknown> = {}
      if (step !== '') patch.approval_step_idx = Number(step)
      if (role !== '') patch.assigned_role = role
      const res = await adminApi.editApprovalState(system, entity, recordId, patch)
      setOpenTasks(res.open_approve_tasks)

      const parts: string[] = []
      if (res.closed_stale_tasks) parts.push(`closed ${res.closed_stale_tasks} stale approve task(s)`)
      if (res.resync_actions?.length) parts.push(...res.resync_actions)
      if (res.routing_resync && res.routing_resync !== 'ok') parts.push(res.routing_resync)
      if (res.reassigned_open_tasks) parts.push(`reassigned ${res.reassigned_open_tasks} task(s)`)

      // The engine legitimately overrides the requested step when the role there is
      // a skippable optional (no Director configured for the department, say) — it
      // advances past it. Correct, but the admin asked for a different number and
      // must not have to infer that from a log line.
      const asked = step === '' ? null : Number(step)
      const landed = res.final_step ?? res.approval_step_idx
      let settled = ''
      if (asked !== null && landed != null && landed !== asked) {
        const label = steps[landed]?.label || steps[landed]?.role || landed
        settled = `Engine settled on step ${landed} (${label}), not ${asked}.`
        setStep(String(landed))
      }

      // The only claim that matters: an approval button exists iff a task is open.
      const verdict = res.open_approve_tasks > 0
        ? `${res.open_approve_tasks} approval task open — the approve button will show.`
        : 'No approval task is open — the approve button will NOT show.'
      setWarn(Boolean(res.resync_warning) || res.open_approve_tasks === 0 || Boolean(settled))
      setMsg([...parts, settled, verdict].filter(Boolean).join(' · '))
    } catch (e) {
      setWarn(true)
      setMsg(e instanceof Error ? e.message : 'Failed')
    } finally { setBusy(false) }
  }

  const roleOptions = steps.map((s) => s.role)

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <p className="mb-2 text-sm font-medium">Approval State (manual override)</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Current step
          {steps.length > 0 ? (
            <select value={step} onChange={(e) => setStep(e.target.value)}
              className="h-8 rounded border border-neutral-300 px-2 text-sm">
              <option value="">— leave unchanged —</option>
              {steps.map((s, i) => (
                <option key={s.id} value={String(i)}>{i} — {s.label || s.role}</option>
              ))}
            </select>
          ) : (
            <input value={step} onChange={(e) => setStep(e.target.value)}
              className="h-8 w-20 rounded border border-neutral-300 px-2 text-sm" />
          )}
        </label>
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Assign task to role
          <select value={role} onChange={(e) => setRole(e.target.value)}
            className="h-8 rounded border border-neutral-300 px-2 text-sm">
            <option value="">— engine decides —</option>
            {roleOptions.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button type="button" onClick={apply} disabled={busy}
          className="h-8 rounded bg-amber-600 px-3 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50">
          {busy ? 'Applying…' : 'Apply'}
        </button>
      </div>

      {openTasks !== null && (
        <p className="mt-2 text-xs text-neutral-600">
          Open approval tasks: <span className="font-medium">{openTasks}</span>
          {openTasks === 0 && ' — nobody can approve this document right now.'}
        </p>
      )}
      {chainError && (
        <p className="mt-1 text-xs text-amber-700">
          Workflow chain unavailable ({chainError}) — the step box falls back to free text.
        </p>
      )}
      {msg && (
        <p className={`mt-2 text-xs ${warn ? 'font-medium text-amber-800' : 'text-neutral-700'}`}>{msg}</p>
      )}
      <p className="mt-1 text-[11px] text-neutral-400">
        Changing the step closes the stale approve task and asks the approval engine to issue
        the one for the new step. Completed tasks and approval history are untouched.
      </p>
    </div>
  )
}
