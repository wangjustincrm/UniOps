/**
 * Immediate and root causes, for an investigation or a hazard.
 *
 * ★ Round 5, answer box 11 put this on the hazard register as well as on an
 * incident investigation. It was 120 lines inline in the investigation page; a
 * second copy for hazards is how two forms that are meant to behave the same stop
 * doing so, which is exactly what round 5's TC-04 caught between the cause
 * editor's action block and the "Raise a corrective action" dialog.
 *
 * The editor owns the rows. The page owns the state and the save — they differ
 * in what else they save alongside, and in when — but both serialise through
 * `causesPayload` and spend their drafts through `spendActionDrafts`, so the two
 * rules that are easy to get subtly wrong are written once.
 */
import { Plus, Trash2 } from 'lucide-react'
import { VocabularySelect, type VocabItem } from '@/components/VocabularyPicker'
import {
  ActionFields, actionIsComplete, emptyActionDraft, type ActionDraft,
} from '@/components/ActionFields'

export interface CauseDraft {
  id?: string
  cause_type: 'immediate' | 'root'
  vocabulary_item_id: string | null
  label: string
  note: string | null
  /** Raised with the cause when it is filled in. Root causes only: an immediate
   *  cause describes what happened, not work to do. */
  action?: ActionDraft
}

/** What the server is sent for a set of causes. An incomplete action box is
 *  left out rather than sent — the server would refuse an unowned action, and a
 *  refusal there would lose the causes typed alongside it. */
export function causesPayload(causes: CauseDraft[]) {
  return causes
    .filter((c) => c.label.trim())
    .map((c) => ({
      cause_type: c.cause_type,
      vocabulary_item_id: c.vocabulary_item_id,
      label: c.label,
      note: c.note || null,
      action: c.action && actionIsComplete(c.action)
        ? {
            title: c.action.title.trim(),
            description: c.action.description || null,
            owner_id: c.action.ownerId,
            due_date: c.action.dueDate,
            priority: c.action.priority,
            hierarchy_of_control: c.action.controlCode,
          }
        : null,
    }))
}

/** ★ Call this the moment the save that carried the causes returns.
 *
 * A save often has more steps after it, and one of them failing leaves the page
 * open with the action still in its state — press save again and the same action
 * is raised a second time, because the cause it hung off may now carry a
 * different label and count as new. Found in round-4 review by uniops-9a on the
 * investigation page, and the hazard page needs exactly the same, which is why it
 * lives here. */
export function spendActionDrafts(causes: CauseDraft[]): CauseDraft[] {
  return causes.map((c) => (c.action ? { ...c, action: undefined } : c))
}

export function CausesEditor({
  type, vocabulary, help, causes, onChange, savedWith,
}: {
  type: 'immediate' | 'root'
  vocabulary: string
  help: string
  causes: CauseDraft[]
  onChange: (next: CauseDraft[]) => void
  /** "the investigation", "the hazard" — finishes "Raised when you save …". */
  savedWith: string
}) {
  const update = (i: number, patch: Partial<CauseDraft>) =>
    onChange(causes.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const remove = (i: number) => onChange(causes.filter((_, j) => j !== i))
  const add = () => onChange([
    ...causes, { cause_type: type, vocabulary_item_id: null, label: '', note: null }])

  return (
    <>
      <p className="mb-2 text-xs text-neutral-500">{help}</p>
      {causes.map((c, i) => c.cause_type !== type ? null : (
        <div key={i} className="mb-2 rounded-lg border border-neutral-200 p-2.5">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <VocabularySelect
                vocabulary={vocabulary}
                value={c.vocabulary_item_id}
                onChange={(item: VocabItem | null) => update(i, {
                  vocabulary_item_id: item?.id ?? null,
                  // Snapshot the wording, per the module's rule for every
                  // vocabulary reference on a record that gets signed.
                  label: item ? item.label : c.label,
                })}
              />
            </div>
            <button type="button" onClick={() => remove(i)}
                    className="flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-danger-600">
              <Trash2 className="h-4 w-4" aria-hidden />
              <span className="sr-only">Remove</span>
            </button>
          </div>
          <input
            value={c.note ?? ''}
            onChange={(e) => update(i, { note: e.target.value })}
            placeholder="Note (optional)"
            className="mt-2 min-h-[38px] w-full rounded-md border border-neutral-200 px-2.5 text-sm focus:border-primary-400 focus:outline-none"
          />
          {/* ★ What is being done about it, on this form. Root causes only — an
              immediate cause describes what happened, and offering a box there
              invites one action per cause where the hierarchy of controls asks
              for one per root cause.

              ★ The fields are the "Raise a corrective action" dialog's own,
              rendered by the same component. Round 5, TC-04: "make the 'What is
              being done about it' form look the same as the 'Raise a corrective
              action' form to ensure consistency." */}
          {type === 'root' && (
            c.action ? (
              <div className="mt-2 rounded-md border border-primary-200 bg-primary-50/40 p-3">
                <p className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-bold text-neutral-900">Corrective action</span>
                  <button type="button" onClick={() => update(i, { action: undefined })}
                          className="text-xs font-normal text-neutral-500 hover:text-danger-600">
                    Not now
                  </button>
                </p>
                <ActionFields value={c.action}
                              onChange={(next) => update(i, { action: next })} />
                <p className={`mt-2 text-[11px] ${
                  actionIsComplete(c.action) ? 'text-neutral-500' : 'text-warning-700'}`}>
                  {actionIsComplete(c.action)
                    ? `Raised when you save ${savedWith}.`
                    : 'Needs what it is, who owns it and when it is due — an '
                      + 'action nobody owns appears in nobody\'s list. Leave the '
                      + 'whole box empty and nothing is raised.'}
                </p>
              </div>
            ) : (
              <button type="button"
                      onClick={() => update(i, { action: emptyActionDraft() })}
                      className="mt-2 flex min-h-[36px] items-center gap-1.5 text-xs font-medium text-primary-700">
                <Plus className="h-3.5 w-3.5" aria-hidden />
                Raise a corrective action for this
              </button>
            )
          )}
        </div>
      ))}
      <button type="button" onClick={add}
              className="flex min-h-[38px] items-center gap-1.5 text-sm font-medium text-primary-700">
        <Plus className="h-4 w-4" aria-hidden />
        Add {type === 'immediate' ? 'an immediate cause' : 'a root cause'}
      </button>
    </>
  )
}
