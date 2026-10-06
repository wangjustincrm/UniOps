/**
 * Building a checklist.
 *
 * A list of questions edited in place rather than a drag-and-drop canvas. HSE's
 * own forms are linear — a racking inspection is nine groups of yes/no in a
 * fixed order — and the thing that makes one hard to build is not arranging
 * boxes, it is getting the fail conditions right. So the editor puts the fail
 * condition next to the question and keeps everything else out of the way.
 *
 * Editing a published template produces the next draft version rather than
 * changing what people have already filled in. That is HSE's audit trail, and
 * it is also what stops a form mutating under someone mid-inspection.
 */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { PageHeader } from '@/components/ui'

interface QuestionType {
  code: string
  label: string
  answerable: boolean
  can_fail: boolean
}

interface Question {
  key: string
  type: string
  label: string
  help?: string
  required?: boolean
  options?: string[]
  vocabulary?: string
  fail_on?: Record<string, unknown> | null
}

interface Version {
  id: string
  version_no: number
  status: string
  schema: Question[]
  published_at: string | null
  published_by_name: string | null
  change_note: string | null
}

const inputClass =
  'min-h-[40px] w-full rounded-lg border border-neutral-300 bg-white px-2.5 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

/** A key derived from the label, since nobody wants to invent one — but kept
 *  stable once set, because answers are stored against it. */
function keyFrom(label: string, taken: Set<string>): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 60)
    || 'question'
  let key = base
  let n = 2
  while (taken.has(key)) key = `${base}_${n++}`
  return key
}

interface VocabularySummary { code: string; name: string }
interface TemplateSummary {
  id: string; code: string; name: string; category: string | null
  /** Whether this kind of form has a verdict at all — the server decides. */
  can_fail?: boolean
}

export default function FormBuilderPage() {
  const { id = '' } = useParams()
  // The lists a `vocabulary` question can point at.
  const template = useQuery({
    queryKey: ['form-template', id],
    queryFn: () => api.get<TemplateSummary>(`/api/v1/forms/templates/${id}`),
    enabled: Boolean(id),
  })
  const vocabularies = useQuery({
    queryKey: ['vocabularies'],
    queryFn: () => api.get<VocabularySummary[]>('/api/v1/settings/vocabularies'),
    staleTime: 10 * 60_000,
  })
  // Older responses may not carry it; a missing answer means "can fail",
  // which is what every template did before this round.
  const kindCanFail = template.data?.can_fail !== false

  const queryClient = useQueryClient()

  const types = useQuery({
    queryKey: ['form-question-types'],
    queryFn: () => api.get<QuestionType[]>('/api/v1/forms/question-types'),
  })
  const versions = useQuery({
    queryKey: ['form-versions', id],
    queryFn: () => api.get<Version[]>(`/api/v1/forms/templates/${id}/versions`),
  })

  const [schema, setSchema] = useState<Question[]>([])
  const [loaded, setLoaded] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const draft = versions.data?.find((v) => v.status === 'draft')
  const published = versions.data?.filter((v) => v.status === 'published') ?? []

  useEffect(() => {
    if (loaded || versions.isLoading) return
    setSchema(draft?.schema ?? published[0]?.schema ?? [])
    setLoaded(true)
  }, [loaded, versions.isLoading, draft, published])

  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/forms/templates/${id}/schema`, { schema }),
    onSuccess: () => {
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['form-versions', id] })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const publish = useMutation({
    mutationFn: async () => {
      await api.put(`/api/v1/forms/templates/${id}/schema`, { schema })
      return api.post(`/api/v1/forms/templates/${id}/publish`, { change_note: note || null })
    },
    onSuccess: () => {
      setNote('')
      queryClient.invalidateQueries({ queryKey: ['form-versions', id] })
      queryClient.invalidateQueries({ queryKey: ['form-templates'] })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const add = (type: string) => {
    const taken = new Set(schema.map((q) => q.key))
    setSchema([...schema, { key: keyFrom(type, taken), type, label: '' }])
    setSaved(false)
  }
  const update = (i: number, patch: Partial<Question>) => {
    setSchema((s) => s.map((q, j) => (j === i ? { ...q, ...patch } : q)))
    setSaved(false)
  }
  const remove = (i: number) => { setSchema((s) => s.filter((_, j) => j !== i)); setSaved(false) }
  const move = (i: number, by: number) => {
    const j = i + by
    if (j < 0 || j >= schema.length) return
    const next = [...schema]
    ;[next[i], next[j]] = [next[j], next[i]]
    setSchema(next)
    setSaved(false)
  }

  if (versions.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>

  const typeOf = (code: string) => types.data?.find((t) => t.code === code)

  return (
    <div className="p-4 pb-28">
      <header className="mb-4">
        {/* Which form this is. The page said "Form builder" and nothing else,
            so with four similarly-named ladder inspections open in tabs there
            was no way to tell them apart. */}
      <PageHeader title={template.data?.name ?? 'Form builder'} />
        {template.data && (
          <p className="text-xs text-neutral-500">
            <span className="font-mono">{template.data.code}</span>
            {template.data.category ? ` · ${template.data.category}` : ''}
            {' · editing the draft'}
          </p>
        )}
        <p className="mt-0.5 text-sm text-neutral-600">
          {published.length > 0
            ? `Published v${published[0].version_no}${
                published[0].published_by_name ? ` by ${published[0].published_by_name}` : ''}. `
            : 'Not published yet. '}
          Editing creates the next version — forms already filled in keep the questions they were
          filled in with.
        </p>
      </header>

      {schema.map((q, i) => {
        const spec = typeOf(q.type)
        const structural = spec && !spec.answerable
        return (
          <div key={i}
               className={`mb-2 rounded-xl border p-3 ${
                 structural ? 'border-neutral-200 bg-neutral-50' : 'border-neutral-200 bg-white'}`}>
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[11px] text-neutral-600">
                {spec?.label ?? q.type}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-neutral-400">
                {q.key}
              </span>
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0}
                      className="flex h-8 w-8 items-center justify-center text-neutral-400 disabled:opacity-30">
                <ChevronUp className="h-4 w-4" aria-hidden /><span className="sr-only">Up</span>
              </button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === schema.length - 1}
                      className="flex h-8 w-8 items-center justify-center text-neutral-400 disabled:opacity-30">
                <ChevronDown className="h-4 w-4" aria-hidden /><span className="sr-only">Down</span>
              </button>
              <button type="button" onClick={() => remove(i)}
                      className="flex h-8 w-8 items-center justify-center text-neutral-400 hover:text-danger-600">
                <Trash2 className="h-4 w-4" aria-hidden /><span className="sr-only">Remove</span>
              </button>
            </div>

            <input className={inputClass} value={q.label}
                   placeholder={structural ? 'Heading or instruction text' : 'The question'}
                   onChange={(e) => update(i, { label: e.target.value })} />

            {!structural && (
              <>
                <input className={`${inputClass} mt-2`} value={q.help ?? ''}
                       placeholder="Help text (optional)"
                       onChange={(e) => update(i, { help: e.target.value || undefined })} />

                {(q.type === 'select' || q.type === 'multi_select') && (
                  <input className={`${inputClass} mt-2`} value={(q.options ?? []).join(', ')}
                         placeholder="Options, comma separated"
                         onChange={(e) => update(i, {
                           options: e.target.value.split(',').map((o) => o.trim()).filter(Boolean),
                         })} />
                )}

                {q.type === 'vocabulary' && (
                  <>
                    {/* Chosen, not typed. This was a free-text box asking for
                        the list's *code* while the lists are known by their
                        names, and the two do not match — "Safety Equipment
                        Type" is `safety_asset_type`. A form went out
                        referencing a list that does not exist, the picker had
                        nothing in it, and the inspection could not be
                        completed. */}
                    <select
                      className={`${inputClass} mt-2`}
                      value={q.vocabulary ?? ''}
                      onChange={(e) => update(i, { vocabulary: e.target.value || undefined })}
                    >
                      <option value="">Choose a list…</option>
                      {(vocabularies.data ?? []).map((v) => (
                        <option key={v.code} value={v.code}>{v.name}</option>
                      ))}
                      {/* A form built before this was a dropdown may point at
                          something that is not on the list. Kept as an option
                          so opening the form does not silently repoint it. */}
                      {q.vocabulary && !(vocabularies.data ?? []).some((v) => v.code === q.vocabulary) && (
                        <option value={q.vocabulary}>{q.vocabulary} — not a list that exists</option>
                      )}
                    </select>
                    {q.vocabulary && !(vocabularies.data ?? []).some((v) => v.code === q.vocabulary) && (
                      <p className="mt-1 text-xs text-danger-700">
                        Nothing answers to “{q.vocabulary}”, so this question will
                        have no options to pick from. Choose a list above.
                      </p>
                    )}
                  </>
                )}

                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-1.5 text-xs text-neutral-700">
                    <input type="checkbox" checked={Boolean(q.required)}
                           onChange={(e) => update(i, { required: e.target.checked || undefined })} />
                    Required
                  </label>

                  {/* ★ Round 5, TC-09: "minutes are a documentation of a
                      meeting, there is no pass or fail in the form nor in the
                      minutes." The box was offered on every yes/no question of
                      every template, so a toolbox talk and a job analysis
                      carried an inspection's apparatus too. Whether this kind
                      of form has a verdict is the server's answer, not each
                      screen's. */}
                  {spec?.can_fail && kindCanFail && (
                    <label className="flex items-center gap-1.5 text-xs text-neutral-700">
                      Fails when
                      <input
                        className="min-h-[32px] w-28 rounded border border-neutral-300 px-2 text-xs"
                        placeholder={q.type === 'number' ? '< 75' : 'No'}
                        value={failToText(q.fail_on)}
                        onChange={(e) => update(i, { fail_on: textToFail(e.target.value) })}
                      />
                    </label>
                  )}
                </div>
                {spec?.can_fail && kindCanFail && q.fail_on && (
                  <p className="mt-1 text-xs text-neutral-500">
                    An answer matching this raises a corrective action when the form is submitted.
                  </p>
                )}
                {/* ★ A condition on a kind of form that has no verdict. The
                    editor does not offer the box for these kinds and the server
                    refuses to save one — so a template that was an inspection
                    and is now minutes could not be saved at all, with nothing on
                    screen to say which question was in the way. It does nothing
                    at fill-in time either. Said plainly, with the way out beside
                    it. Round 5 review (uniops-41). */}
                {!kindCanFail && q.fail_on && (
                  <div className="mt-1.5 rounded-md bg-warning-50 px-2.5 py-2">
                    <p className="text-xs text-warning-800">
                      This question still has a fail condition from when the form
                      was a different kind. It does nothing now — a
                      {' '}{template.data?.category} form has no pass or fail —
                      and the form cannot be saved until it is removed.
                    </p>
                    <button type="button"
                            className="mt-1.5 min-h-[32px] rounded border border-warning-500 bg-white px-2 text-xs font-semibold text-warning-800"
                            onClick={() => update(i, { fail_on: null })}>
                      Remove the fail condition
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )
      })}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {types.data?.map((t) => (
          <button key={t.code} type="button" onClick={() => add(t.code)}
                  className="flex min-h-[36px] items-center gap-1 rounded-full border border-neutral-300 bg-white px-2.5 text-xs font-medium text-neutral-700 hover:border-primary-300">
            <Plus className="h-3 w-3" aria-hidden />
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
      )}

      <div className="fixed inset-x-0 bottom-0 border-t border-neutral-200 bg-white/95 p-3 backdrop-blur">
        <div className="mx-auto flex max-w-2xl items-center gap-2">
          <input className={`${inputClass} flex-1`} value={note}
                 placeholder="What changed (recorded on the version)"
                 onChange={(e) => setNote(e.target.value)} />
          <button type="button" disabled={save.isPending}
                  onClick={() => { setError(null); save.mutate() }}
                  className="min-h-[44px] rounded-lg border border-neutral-300 px-3 text-sm font-medium text-neutral-700 disabled:opacity-50">
            {save.isPending ? 'Saving…' : saved ? 'Saved' : 'Save draft'}
          </button>
          <button type="button" disabled={publish.isPending || schema.length === 0}
                  onClick={() => { setError(null); publish.mutate() }}
                  className="min-h-[44px] rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50">
            {publish.isPending ? 'Publishing…' : 'Publish'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** `{equals:'No'}` <-> `No`, `{lt:75}` <-> `< 75`. A small language, kept small:
 *  every failing question in every form HSE sent is one of these two shapes. */
function failToText(fail: Record<string, unknown> | null | undefined): string {
  if (!fail) return ''
  if ('equals' in fail) return String(fail.equals)
  if ('in' in fail) return (fail.in as unknown[]).join(', ')
  if ('gt' in fail) return `> ${fail.gt}`
  if ('lt' in fail) return `< ${fail.lt}`
  return ''
}

function textToFail(text: string): Record<string, unknown> | null {
  const t = text.trim()
  if (!t) return null
  const m = /^([<>])\s*(-?\d+(?:\.\d+)?)$/.exec(t)
  if (m) return m[1] === '>' ? { gt: Number(m[2]) } : { lt: Number(m[2]) }
  if (t.includes(',')) return { in: t.split(',').map((x) => x.trim()).filter(Boolean) }
  return { equals: t }
}
