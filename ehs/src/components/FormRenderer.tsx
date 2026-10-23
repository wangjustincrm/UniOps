/**
 * Rendering a form from its schema.
 *
 * The schema comes from the submission's snapshot, not from the template, so a
 * form does not change shape under someone halfway through filling it in. This
 * component never fetches — it renders what it is given and reports answers
 * back by question key.
 *
 * Built for a phone held in one hand at the point of inspection: every control
 * clears 44px, groups are headings rather than collapsible sections (one more
 * tap between a person and the next question is one they will not make while
 * standing at a rack), and the whole form is one scroll rather than a wizard.
 */
import { useState } from 'react'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { PhotoCapture } from '@/components/PhotoCapture'
import { SignaturePad } from '@/components/SignaturePad'
import { LocationSelect } from '@/components/LocationSelect'
import { VocabularySelect } from '@/components/VocabularyPicker'
import type { CompressedImage } from '@/lib/imageCompress'

export interface Question {
  key: string
  type: string
  label: string
  help?: string
  required?: boolean
  /** Round 9: bold and larger wherever the form is shown — filling it in,
   *  reading it back, and printed. Set per question in the form builder. */
  emphasis?: boolean
  options?: string[]
  vocabulary?: string
  fail_on?: Record<string, unknown>
}

/** Answers keyed by question. Shapes match what the API normalises. */
export type FormValues = Record<string, unknown>

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

const YES_NO_NA = ['Yes', 'No', 'N/A'] as const

/** Whether this answer trips the question's fail condition — mirrors the
 *  server's rule so a failing answer is visible as you record it rather than
 *  only after submitting. The server decides; this is feedback, not authority. */
export function looksLikeFailure(q: Question, value: unknown): boolean {
  const cond = q.fail_on
  if (!cond || value === null || value === undefined || value === '') return false
  const s = String(value).trim().toLowerCase()
  if ('equals' in cond) return s === String(cond.equals).trim().toLowerCase()
  if ('in' in cond) return (cond.in as unknown[]).map((v) => String(v).toLowerCase()).includes(s)
  const n = Number(value)
  if (Number.isNaN(n)) return false
  if ('gt' in cond && n > Number(cond.gt)) return true
  if ('lt' in cond && n < Number(cond.lt)) return true
  return false
}

function Label({ q }: { q: Question }) {
  return (
    <>
      <label className={q.emphasis
        ? 'mb-1 block text-base font-bold text-neutral-900'
        : 'mb-1 block text-sm font-medium text-neutral-800'}>
        {q.label}
        {q.required && <span className="ml-1 text-danger-600">*</span>}
      </label>
      {q.help && <p className="mb-1.5 text-xs text-neutral-500">{q.help}</p>}
    </>
  )
}

export function FormRenderer({
  schema, values, onChange, photos, onPhotosChange, savedSignature, disabled = false,
}: {
  schema: Question[]
  values: FormValues
  onChange: (key: string, value: unknown) => void
  photos: Record<string, CompressedImage[]>
  onPhotosChange: (key: string, next: CompressedImage[]) => void
  savedSignature?: string | null
  disabled?: boolean
}) {
  const set = (k: string) => (v: unknown) => { if (!disabled) onChange(k, v) }

  return (
    <div>
      {schema.map((q) => {
        // ── Structure, not questions ──────────────────────────────────────
        if (q.type === 'group') {
          return (
            <h3 key={q.key}
                className="mb-2 mt-6 border-b border-neutral-200 pb-1.5 text-xs font-bold uppercase tracking-wider text-neutral-500 first:mt-0">
              {q.label}
            </h3>
          )
        }
        if (q.type === 'instruction') {
          return (
            <p key={q.key}
               className="mb-4 rounded-lg bg-neutral-50 px-3 py-2.5 text-sm text-neutral-600">
              {q.label}
            </p>
          )
        }

        const value = values[q.key]
        const failing = looksLikeFailure(q, value)

        return (
          <div key={q.key} className="mb-4">
            <Label q={q} />

            {q.type === 'yes_no_na' && (
              <div className="flex gap-2">
                {YES_NO_NA.map((opt) => {
                  const on = String(value ?? '') === opt
                  // A failing choice is coloured only once chosen. Colouring
                  // "No" before anyone picks it reads as a warning about the
                  // question rather than about the answer.
                  const bad = on && looksLikeFailure(q, opt)
                  return (
                    <button
                      key={opt} type="button" disabled={disabled}
                      onClick={() => set(q.key)(opt)}
                      aria-pressed={on}
                      className={[
                        'min-h-[44px] flex-1 rounded-lg border text-sm font-medium transition-colors',
                        on
                          ? bad
                            ? 'border-danger-300 bg-danger-50 text-danger-700'
                            : 'border-primary-300 bg-primary-50 text-primary-700'
                          : 'border-neutral-300 bg-white text-neutral-700',
                      ].join(' ')}
                    >
                      {opt}
                    </button>
                  )
                })}
              </div>
            )}

            {q.type === 'text' && (
              <input className={inputClass} disabled={disabled}
                     value={(value as string) ?? ''}
                     onChange={(e) => set(q.key)(e.target.value)} />
            )}

            {q.type === 'textarea' && (
              <textarea rows={3} className={`${inputClass} py-2`} disabled={disabled}
                        value={(value as string) ?? ''}
                        onChange={(e) => set(q.key)(e.target.value)} />
            )}

            {q.type === 'number' && (
              <input type="number" inputMode="decimal" disabled={disabled}
                     className={`${inputClass} ${failing ? 'border-danger-300 bg-danger-50' : ''}`}
                     value={(value as string) ?? ''}
                     onChange={(e) => set(q.key)(e.target.value)} />
            )}

            {q.type === 'date' && (
              <input type="date" className={inputClass} disabled={disabled}
                     value={(value as string) ?? ''}
                     onChange={(e) => set(q.key)(e.target.value)} />
            )}

            {/* Separate from date because HSE's forms ask them as two fields. */}
            {q.type === 'time' && (
              <input type="time" className={inputClass} disabled={disabled}
                     value={(value as string) ?? ''}
                     onChange={(e) => set(q.key)(e.target.value)} />
            )}

            {q.type === 'select' && (
              <select className={`${inputClass} ${failing ? 'border-danger-300 bg-danger-50' : ''}`}
                      disabled={disabled} value={(value as string) ?? ''}
                      onChange={(e) => set(q.key)(e.target.value)}>
                <option value="">Select…</option>
                {(q.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            )}

            {q.type === 'multi_select' && (
              <div className="flex flex-wrap gap-1.5">
                {(q.options ?? []).map((o) => {
                  const chosen = Array.isArray(value) && (value as string[]).includes(o)
                  return (
                    <button
                      key={o} type="button" disabled={disabled}
                      onClick={() => {
                        const cur = Array.isArray(value) ? (value as string[]) : []
                        set(q.key)(chosen ? cur.filter((x) => x !== o) : [...cur, o])
                      }}
                      className={[
                        'min-h-[40px] rounded-full border px-3 text-xs font-medium',
                        chosen
                          ? 'border-primary-300 bg-primary-50 text-primary-700'
                          : 'border-neutral-300 bg-white text-neutral-700',
                      ].join(' ')}
                    >
                      {o}
                    </button>
                  )
                })}
              </div>
            )}

            {q.type === 'person_multi' && (
              <PeopleField
                value={value}
                onChange={(next) => set(q.key)(next.length ? next : null)}
              />
            )}

            {q.type === 'person' && (
              <PersonPicker
                // Read back through the same translation it is written with.
                // The answer is stored as {id,label} — the shape the server
                // snapshots the name from — and this used to hand that
                // straight to a picker expecting {userId,name}, so every field
                // went blank the moment it was filled in. It read as the
                // selection not sticking, and it stopped the inspection being
                // finished at all.
                value={toPersonValue(value)}
                onChange={(p) => set(q.key)(
                  p.userId ? { id: p.userId, label: p.name } : p.name || null)}
              />
            )}

            {q.type === 'vocabulary' && q.vocabulary && (
              <VocabularySelect
                vocabulary={q.vocabulary}
                value={(value as { id?: string } | null)?.id ?? null}
                onChange={(item) => set(q.key)(item ? { id: item.id, label: item.label } : null)}
              />
            )}

            {(q.type === 'location' || q.type === 'department') && (
              <LocationSelect
                value={(value as { id?: string } | null)?.id ?? null}
                disabled={disabled}
                onChange={(id, path) => set(q.key)(id ? { id, label: path } : null)}
              />
            )}

            {q.type === 'photo' && (
                <PhotoCapture photos={photos[q.key] ?? []}
                              onChange={(next) => onPhotosChange(q.key, next)} />
            )}

            {q.type === 'signature' && (
              <SignaturePad value={(value as string) ?? null} onChange={set(q.key)}
                            savedSignature={savedSignature ?? null} />
            )}

            {failing && (
              <p className="mt-1.5 text-xs font-medium text-danger-700">
                This will raise a corrective action when the form is submitted.
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** One stored answer, as the person picker wants it.
 *
 *  Answers travel as `{id, label}` for an employee and a bare string for
 *  somebody who is not one — a contractor, a visitor. The picker speaks
 *  `PersonValue`. Keeping the two apart is deliberate: the server stores a
 *  name snapshot beside the id so a record three years old still reads as it
 *  did. What was missing was the way back.
 */
export function toPersonValue(stored: unknown): PersonValue {
  if (!stored) return EMPTY_PERSON
  if (typeof stored === 'string') {
    return { ...EMPTY_PERSON, name: stored }
  }
  const v = stored as { id?: string; label?: string; userId?: string; name?: string }
  // Tolerates a PersonValue too: a draft saved before this existed may hold one.
  if (v.userId || v.name) {
    return { ...EMPTY_PERSON, ...(stored as PersonValue) }
  }
  return {
    ...EMPTY_PERSON,
    userId: v.id ?? null,
    name: v.label ?? '',
  }
}

/**
 * A question that takes several people.
 *
 * The type has always been offered — "Several people", stored as a list of
 * ids — and was rendered with the single picker, so it could only ever hold
 * one. A form asking who else was involved recorded exactly one of them.
 */
function PeopleField({
  value, onChange,
}: {
  value: unknown
  onChange: (next: { id: string; label: string }[]) => void
}) {
  const [adding, setAdding] = useState<PersonValue>(EMPTY_PERSON)
  const chosen: { id: string; label: string }[] = Array.isArray(value)
    ? (value as unknown[]).map((v) => {
        const p = toPersonValue(v)
        return { id: p.userId ?? p.name, label: p.name }
      }).filter((p) => p.label)
    : []

  return (
    <div>
      <PersonPicker
        value={adding}
        placeholder="Add somebody"
        onChange={(p) => {
          const id = p.userId ?? p.name
          if (id && !chosen.some((c) => c.id === id)) {
            onChange([...chosen, { id, label: p.name }])
          }
          setAdding(EMPTY_PERSON)
        }}
      />
      {chosen.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {chosen.map((c) => (
            <li key={c.id}
                className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
              {c.label}
              <button type="button" aria-label={`Remove ${c.label}`}
                      onClick={() => onChange(chosen.filter((x) => x.id !== c.id))}
                      className="text-neutral-400 hover:text-neutral-700">
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
