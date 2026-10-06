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
import {
  ActionFields, actionIsComplete, emptyActionDraft, type ActionDraft,
} from '@/components/ActionFields'

export function NewActionDialog({
  incidentId, causeId, causeLabel, onClose,
}: {
  incidentId: string
  causeId: string
  causeLabel: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<ActionDraft>(emptyActionDraft)
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/actions', {
      source_type: 'incident',
      source_id: incidentId,
      cause_id: causeId,
      action_type: 'corrective',
      title: draft.title,
      description: draft.description || null,
      hierarchy_of_control: draft.controlCode,
      priority: draft.priority,
      owner_id: draft.ownerId,
      due_date: draft.dueDate,
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
  const canSave = actionIsComplete(draft)

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

        <ActionFields value={draft} onChange={setDraft} />

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
