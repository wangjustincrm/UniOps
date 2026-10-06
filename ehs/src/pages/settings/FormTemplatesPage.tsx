/**
 * The forms HSE maintains, and the schedules that hand them out.
 *
 * Templates and schedules sit on one page because they are one decision: a
 * checklist nobody is asked to fill in is a document, and a schedule pointing
 * at nothing cannot exist. Keeping them apart would let either half be created
 * without the other and look complete.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, CalendarClock, ClipboardList, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import {
  dangerButton, ErrorNote, PageHeader, secondaryButton,
} from '@/components/ui'

interface Template {
  id: string; code: string; name: string; description: string | null
  category: string; is_active: boolean
}
interface References {
  submissions: number; schedules: number; equipment: number
  /** 1 when this is the form Settings names for the committee's minutes. */
  settings: number
}

interface Schedule {
  id: string; template_id: string; name: string; frequency: string
  rotation: string; next_due_on: string; is_active: boolean; assigned_role: string | null
}

const inputClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'focus:border-primary-400 focus:outline-none'

export default function FormTemplatesPage() {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [scheduling, setScheduling] = useState<Template | null>(null)

  // ★ Round 5, box 7: "provide a way to delete or archive all the forms."
  // Archived ones are shown on request rather than vanishing, because the way
  // back has to be somewhere — an archive you cannot see is a delete.
  const [showArchived, setShowArchived] = useState(false)
  const templates = useQuery({
    queryKey: ['form-templates', showArchived],
    queryFn: () => api.get<Template[]>(
      `/api/v1/forms/templates${showArchived ? '?include_inactive=true' : ''}`),
  })
  const schedules = useQuery({
    queryKey: ['form-schedules'],
    queryFn: () => api.get<Schedule[]>('/api/v1/forms/schedules'),
  })

  const byTemplate = new Map<string, Schedule[]>()
  for (const s of schedules.data ?? []) {
    byTemplate.set(s.template_id, [...(byTemplate.get(s.template_id) ?? []), s])
  }

  return (
    <div className="p-4">
      <PageHeader title="Forms">
        <button type="button" onClick={() => setAdding(true)}
                className="flex min-h-[40px] items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white">
          <Plus className="h-4 w-4" aria-hidden />
          New form
        </button>
      </PageHeader>

      <label className="mb-3 flex items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" checked={showArchived}
               onChange={(e) => setShowArchived(e.target.checked)} />
        Include archived forms
      </label>

      {templates.data?.length === 0 && (
        <p className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">
          No forms yet. Create one, add its questions, then publish it.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {templates.data?.map((t) => (
          <li key={t.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
            <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
              <ClipboardList className="mt-0.5 h-4 w-4 shrink-0 text-primary-600" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-neutral-900">{t.name}</span>
                <span className="mt-0.5 block font-mono text-xs text-neutral-400">{t.code}</span>
              </span>
              <Link to={`/settings/forms/${t.id}`}
                    className="min-h-[36px] rounded-lg border border-neutral-300 px-2.5 pt-1.5 text-xs font-medium text-neutral-700">
                Questions
              </Link>
              <button type="button" onClick={() => setScheduling(t)}
                      className="flex min-h-[36px] items-center gap-1 rounded-lg border border-neutral-300 px-2.5 text-xs font-medium text-neutral-700">
                <CalendarClock className="h-3.5 w-3.5" aria-hidden />
                Schedule
              </button>
              <TemplateLifecycle template={t} onChanged={() => {
                queryClient.invalidateQueries({ queryKey: ['form-templates'] })
                queryClient.invalidateQueries({ queryKey: ['form-schedules'] })
              }} />
            </div>
            {!t.is_active && (
              <p className="mt-1.5 text-xs text-neutral-500">
                Archived — not offered on any form list. Everything filled in on
                it is still readable.
              </p>
            )}

            {(byTemplate.get(t.id) ?? []).map((s) => (
              <p key={s.id} className="mt-2 rounded-md bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-600">
                {s.name} · {s.frequency}
                {s.rotation === 'department' && ' · one department per cycle'}
                {' · next '}{s.next_due_on}
                {s.assigned_role ? ` · ${s.assigned_role}` : ''}
                {!s.is_active && ' · paused'}
              </p>
            ))}
          </li>
        ))}
      </ul>

      {adding && <NewTemplateDialog onClose={() => setAdding(false)}
                                    onDone={() => queryClient.invalidateQueries({ queryKey: ['form-templates'] })} />}
      {scheduling && <NewScheduleDialog template={scheduling} onClose={() => setScheduling(null)}
                                        onDone={() => queryClient.invalidateQueries({ queryKey: ['form-schedules'] })} />}
    </div>
  )
}

function NewTemplateDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [category, setCategory] = useState('inspection')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/forms/templates', {
      // Derived from the name, because a code nobody chose is one nobody has to
      // think about — but editable, since it appears on every submission number.
      code: code || name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40),
      name, category, description: null,
    }),
    onSuccess: () => { onDone(); onClose() },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="w-full rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">New form</h2>
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Name</label>
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Warehouse Monthly Racking Safety Inspection" />
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Code</label>
        <input className={inputClass} value={code} onChange={(e) => setCode(e.target.value)}
               placeholder="Left blank, derived from the name" />
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Kind</label>
        <select className={inputClass} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="inspection">Inspection</option>
          <option value="jsa">JSA</option>
          <option value="permit">Permit</option>
          <option value="orientation">Orientation</option>
          <option value="toolbox_talk">Toolbox talk</option>
          <option value="audit">Audit</option>
          <option value="other">Other</option>
        </select>
        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={!name.trim() || create.isPending}
                  onClick={() => { if (name.trim() && !create.isPending) { setError(null); create.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {create.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}

function NewScheduleDialog({ template, onClose, onDone }: {
  template: Template; onClose: () => void; onDone: () => void
}) {
  const [name, setName] = useState(`${template.name} — recurring`)
  const [frequency, setFrequency] = useState('monthly')
  const [rotation, setRotation] = useState('none')
  const [role, setRole] = useState('ehs_coordinator')
  // A week out, read off the local calendar. `toISOString` would push this a
  // day late for anyone west of UTC, which at UTC-4 means every evening.
  const [nextDue, setNextDue] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 7)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${
      String(d.getDate()).padStart(2, '0')}`
  })
  const [departments, setDepartments] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const depts = useQuery({
    queryKey: ['departments'],
    queryFn: () => api.get<{ id: string; name: string }[]>('/api/v1/settings/departments'),
    retry: false,
  })

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/forms/schedules', {
      template_id: template.id, name, frequency, rotation,
      department_ids: rotation === 'department' ? departments : [],
      location_ids: [], assigned_role: role, lead_days: 7,
      next_due_on: nextDue, is_active: true,
    }),
    onSuccess: () => { onDone(); onClose() },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">Schedule</h2>
        <p className="mt-0.5 text-xs text-neutral-500">{template.name}</p>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Name</label>
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />

        <div className="mt-3 grid grid-cols-2 gap-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-neutral-800">How often</label>
            <select className={inputClass} value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              {['daily', 'weekly', 'monthly', 'quarterly', 'annual'].map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-neutral-800">First due</label>
            <input type="date" className={inputClass} value={nextDue}
                   onChange={(e) => setNextDue(e.target.value)} />
          </div>
        </div>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Coverage</label>
        <select className={inputClass} value={rotation} onChange={(e) => setRotation(e.target.value)}>
          <option value="none">The same place every time</option>
          <option value="department">One department per cycle, in turn</option>
        </select>
        {rotation === 'department' && (
          <>
            <p className="mb-1.5 mt-2 text-xs text-neutral-500">
              Tick the departments in the order they should come round. This is how the JHSC
              inspection works — one a month, cycling through.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {(depts.data ?? []).map((d) => {
                const on = departments.includes(d.id)
                return (
                  <button key={d.id} type="button"
                          onClick={() => setDepartments(on
                            ? departments.filter((x) => x !== d.id)
                            : [...departments, d.id])}
                          className={`min-h-[36px] rounded-full border px-2.5 text-xs font-medium ${
                            on ? 'border-primary-300 bg-primary-50 text-primary-700'
                               : 'border-neutral-300 bg-white text-neutral-700'}`}>
                    {on && `${departments.indexOf(d.id) + 1}. `}{d.name}
                  </button>
                )
              })}
            </div>
            {depts.isError && (
              <p className="mt-1 text-xs text-neutral-500">
                Department list unavailable — the rotation can be set from the API for now.
              </p>
            )}
          </>
        )}

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Who is asked</label>
        <select className={inputClass} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="ehs_coordinator">HSE Coordinator</option>
          <option value="ehs_manager">HSE Manager</option>
          <option value="jhsc_member">JHSC Member</option>
          <option value="area_supervisor">Area Supervisor</option>
        </select>

        {error && <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={create.isPending}
                  onClick={() => { if (!create.isPending) { setError(null); create.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {create.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Archive, restore, delete.
 *
 * ★ Round 5, box 7: "provide a way to delete or archive all the forms."
 * `is_active` had been on the template since the first migration with no
 * endpoint to set it, so nothing could ever be taken out of the list.
 *
 * What refers to the form is fetched *before* the question is asked, so the
 * confirmation can say what will happen rather than the person finding out from
 * a refusal. Delete is only offered when nothing points at it at all — the
 * server refuses anyway, and offering a button that always fails is how people
 * learn to distrust the others.
 */
function TemplateLifecycle({ template, onChanged }: {
  template: Template; onChanged: () => void
}) {
  const [asking, setAsking] = useState(false)
  const refs = useQuery({
    queryKey: ['form-template-refs', template.id],
    queryFn: () => api.get<References>(
      `/api/v1/forms/templates/${template.id}/references`),
    enabled: asking,
  })

  const act = useMutation({
    mutationFn: (what: 'archive' | 'restore' | 'delete') => (
      what === 'delete'
        ? api.del(`/api/v1/forms/templates/${template.id}`)
        : api.post(`/api/v1/forms/templates/${template.id}/${what}`, {})
    ),
    onSuccess: () => { setAsking(false); onChanged() },
  })

  const used = refs.data
    ? refs.data.submissions + refs.data.schedules + refs.data.equipment
      + refs.data.settings
    : null

  if (!asking) {
    return (
      <button type="button" onClick={() => setAsking(true)}
              className="flex min-h-[36px] items-center gap-1 rounded-lg border border-neutral-300 px-2.5 text-xs font-medium text-neutral-700">
        <Archive className="h-3.5 w-3.5" aria-hidden />
        {template.is_active ? 'Archive' : 'Restore'}
      </button>
    )
  }

  return (
    <div className="w-full rounded-lg border border-neutral-300 bg-neutral-50 p-2.5">
      {refs.isLoading && <p className="text-xs text-neutral-500">Checking what uses it…</p>}
      {refs.isError && <p className="text-xs text-danger-700">
        Could not check what uses this form. Nothing has been changed.
      </p>}
      {/* ★ Restoring does not switch the schedules back on, on purpose: a monthly
          form archived six months ago would raise six overdue rounds the moment
          it came back. Said here because an undocumented omission and a
          deliberate one look the same from outside. */}
      {!template.is_active && (
        <p className="mb-1.5 text-xs text-neutral-600">
          Putting it back makes it available to fill in again. Its schedule stays
          off — start it again from <b>Schedule</b> when you want the rounds to
          resume.
        </p>
      )}
      {refs.data && (
        <p className="text-xs text-neutral-700">
          {used === 0
            ? 'Nothing has been filled in on this form and nothing points at it.'
            : `${refs.data.submissions} filled in · ${refs.data.schedules} schedule(s) · ${refs.data.equipment} item(s) scan to it.`
              + (refs.data.settings ? ' This is the committee’s minutes form.' : '')}
        </p>
      )}
      <ErrorNote error={act.error} />
      <div className="mt-2 flex flex-wrap gap-2">
        {template.is_active ? (
          <button type="button" className={secondaryButton} disabled={act.isPending}
                  onClick={() => { if (!act.isPending) act.mutate('archive') }}>
            {act.isPending ? 'Working…' : 'Archive it'}
          </button>
        ) : (
          <button type="button" className={secondaryButton} disabled={act.isPending}
                  onClick={() => { if (!act.isPending) act.mutate('restore') }}>
            {act.isPending ? 'Working…' : 'Put it back'}
          </button>
        )}
        {used === 0 && (
          <button type="button" className={dangerButton} disabled={act.isPending}
                  onClick={() => { if (!act.isPending) act.mutate('delete') }}>
            Delete it for good
          </button>
        )}
        <button type="button" className={secondaryButton} onClick={() => setAsking(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}
