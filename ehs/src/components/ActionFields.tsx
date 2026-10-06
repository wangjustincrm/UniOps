/**
 * The fields of a corrective action, in one place.
 *
 * ★ Round 5, TC-04: "make the 'What is being done about it' form look the same as
 * the 'Raise a corrective action' form to ensure consistency."
 *
 * There were two copies. The dialog that raises an action against a cause asked
 * for what, detail, owner, due date, priority and type of control, each with a
 * label. The block inside an investigation — and now a hazard — asked for four of
 * the six, with placeholders where the labels should be. The same action raised
 * two ways came out different, and looked different while it was being raised.
 *
 * Both render this now. It holds no state of its own: the dialog keeps its draft
 * in local state, the cause editor keeps one per cause inside the set it saves,
 * and both hand the same shape in and out.
 */
import { PersonPicker, EMPTY_PERSON } from '@/components/PersonPicker'
import { VocabularySelect } from '@/components/VocabularyPicker'

export interface ActionDraft {
  title: string
  description: string
  ownerId: string | null
  /** Shown in the picker; the server is sent the id. */
  ownerName: string
  dueDate: string
  priority: 'low' | 'normal' | 'high'
  /** The vocabulary item, for the picker. */
  controlId: string | null
  /** Its code, which is what the server stores. */
  controlCode: string | null
}

/** Ontario-local, so a due date defaulting to "two weeks" does not land a day
 *  out for anyone west of UTC. */
export function inDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function emptyActionDraft(): ActionDraft {
  return {
    title: '', description: '', ownerId: null, ownerName: '',
    dueDate: inDays(14), priority: 'normal', controlId: null, controlCode: null,
  }
}

/** What the server needs before it will raise one. An action nobody owns has no
 *  inbox to appear in and no escalation ladder to climb. */
export function actionIsComplete(a: ActionDraft): boolean {
  return Boolean(a.title.trim() && a.ownerId && a.dueDate)
}

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

const labelClass = 'mb-1 block text-sm font-medium text-neutral-800'

export function ActionFields({ value, onChange }: {
  value: ActionDraft
  onChange: (next: ActionDraft) => void
}) {
  const set = <K extends keyof ActionDraft>(key: K, v: ActionDraft[K]) =>
    onChange({ ...value, [key]: v })

  return (
    <>
      <label className={labelClass}>What will be done</label>
      <input className={inputClass} value={value.title}
             onChange={(e) => set('title', e.target.value)}
             placeholder="Fit an interlocked guard to the capping head" />

      <label className={`${labelClass} mt-3`}>Detail</label>
      <textarea rows={3} value={value.description}
                onChange={(e) => set('description', e.target.value)}
                className={`${inputClass} py-2`} />

      <label className={`${labelClass} mt-3`}>Owner</label>
      <PersonPicker
        value={{ ...EMPTY_PERSON, userId: value.ownerId, name: value.ownerName }}
        allowExternal={false}
        onChange={(v) => onChange({ ...value, ownerId: v.userId, ownerName: v.name })}
      />

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <label className={labelClass}>Due</label>
          <input type="date" className={inputClass} value={value.dueDate}
                 onChange={(e) => set('dueDate', e.target.value)} />
        </div>
        <div>
          <label className={labelClass}>Priority</label>
          <select className={inputClass} value={value.priority}
                  onChange={(e) => set('priority', e.target.value as ActionDraft['priority'])}>
            <option value="low">Low</option>
            <option value="normal">Normal</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>

      <label className={`${labelClass} mt-3`}>Type of control</label>
      <p className="mb-1.5 text-xs text-neutral-500">
        Elimination is the strongest and PPE the weakest — recording which one this
        is shows whether the plant is fixing hazards or working around them.
      </p>
      <VocabularySelect
        vocabulary="hierarchy_of_control"
        value={value.controlId}
        onChange={(item) => onChange({
          ...value, controlId: item?.id ?? null, controlCode: item?.code ?? null })}
        placeholder="Not specified"
      />
    </>
  )
}
