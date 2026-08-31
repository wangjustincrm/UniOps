/**
 * Raising a corrective action against a root cause, from where the gap is visible.
 *
 * The cause tree already flags a root cause with nothing being done about it —
 * that gap is what an auditor looks for — but until now the only way to close it
 * was to leave, open the actions list, and re-key which incident and which cause
 * it belonged to. Nobody does that reliably, so the flag stayed red on causes
 * that had in fact been dealt with.
 *
 * Opening from the cause carries `cause_id` through, which is what lets the tree
 * show the action underneath it afterwards.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { VocabularySelect } from '@/components/VocabularyPicker'

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

/** Ontario-local today, so a due date defaulting to "next week" does not land a
 *  day out for anyone west of UTC. */
function inDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function NewActionDialog({
  incidentId, causeId, causeLabel, onClose,
}: {
  incidentId: string
  causeId: string
  causeLabel: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [owner, setOwner] = useState<PersonValue>(EMPTY_PERSON)
  const [dueDate, setDueDate] = useState(inDays(14))
  const [control, setControl] = useState<string | null>(null)
  const [controlCode, setControlCode] = useState<string | null>(null)
  const [priority, setPriority] = useState('normal')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/actions', {
      source_type: 'incident',
      source_id: incidentId,
      cause_id: causeId,
      action_type: 'corrective',
      title,
      description: description || null,
      hierarchy_of_control: controlCode,
      priority,
      owner_id: owner.userId,
      due_date: dueDate,
    }),
    onSuccess: () => {
      // The tree renders the new action under its cause and drops the flag.
      queryClient.invalidateQueries({ queryKey: ['incident', incidentId, 'causes'] })
      onClose()
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  // An action with no owner has nobody to chase, and the escalation chain has
  // nobody to escalate to.
  const canSave = title.trim() && owner.userId && dueDate

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-lg sm:rounded-2xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-bold text-neutral-900">Corrective action</h2>
            <p className="mt-0.5 truncate text-xs text-neutral-500">
              Against: {causeLabel}
            </p>
          </div>
          <button type="button" onClick={onClose}
                  className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400">
            <X className="h-5 w-5" aria-hidden />
            <span className="sr-only">Close</span>
          </button>
        </div>

        <label className="mb-1 block text-sm font-medium text-neutral-800">
          What will be done
        </label>
        <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
               placeholder="Fit an interlocked guard to the capping head" />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          Detail
        </label>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                  className={`${inputClass} py-2`} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Owner</label>
        <PersonPicker value={owner} onChange={setOwner} />

        <div className="mt-3 grid grid-cols-2 gap-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-neutral-800">Due</label>
            <input type="date" className={inputClass} value={dueDate}
                   onChange={(e) => setDueDate(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-neutral-800">Priority</label>
            <select className={inputClass} value={priority}
                    onChange={(e) => setPriority(e.target.value)}>
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
            </select>
          </div>
        </div>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          Type of control
        </label>
        <p className="mb-1.5 text-xs text-neutral-500">
          Elimination is the strongest and PPE the weakest — recording which one this
          is shows whether the plant is fixing hazards or working around them.
        </p>
        <VocabularySelect
          vocabulary="hierarchy_of_control"
          value={control}
          onChange={(item) => { setControl(item?.id ?? null); setControlCode(item?.code ?? null) }}
          placeholder="Not specified"
        />

        {error && (
          <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
        )}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSave || create.isPending}
            onClick={() => { if (canSave && !create.isPending) { setError(null); create.mutate() } }}
            className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50"
          >
            {create.isPending ? 'Creating…' : 'Create action'}
          </button>
        </div>
      </div>
    </div>
  )
}
