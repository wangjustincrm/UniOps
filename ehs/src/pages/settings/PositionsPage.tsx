/**
 * Job positions and what each one requires.
 *
 * This page exists to make the training compliance report tell the truth. That
 * report treats a course as required of somebody if it applies to everyone, or
 * if a position they hold requires it. HSE named four of each — WHMIS, Lockout
 * Tagout, Safety Awareness and Working at Heights for all staff; Forklift,
 * Mobile Equipment Work Platform, Confined Space and First Aid for particular
 * jobs. Without somewhere to define positions, that second four could never be
 * required of anyone, and the report an inspector asks for first was silently
 * answering half the question.
 *
 * So the two counts on each row — who holds it, what it needs — are the point
 * of the layout. A position with requirements and no holders, or holders and
 * no requirements, is a matrix somebody started and did not finish, and it
 * reads as such at a glance.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, Plus, UserMinus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'

interface Position {
  id: string; code: string; name: string; is_active: boolean
  holders: number; requirements: number
}
interface Requirement {
  id: string; requirement_type: string; course_id: string | null
  vocabulary_item_id: string | null; label: string | null; is_mandatory: boolean
}
interface Holder {
  assignment_id: string; user_id: string; user_name: string; effective_from: string
}
interface Course {
  id: string; code: string; name: string; is_statutory: boolean; applies_to_all: boolean
}

export default function PositionsPage() {
  const { can } = usePermissions()
  const mayWrite = can('ehs.worker.write')
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const positions = useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/api/v1/positions'),
  })

  return (
    <div className="p-4">
      <PageHeader
        title="Job positions"
        subtitle="What each job requires — training compliance counts a course against the people whose position requires it"
      >
        {mayWrite && (
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a position
          </button>
        )}
      </PageHeader>

      {creating && <NewPositionForm onClose={() => setCreating(false)} />}

      {positions.data?.length === 0 && !creating ? (
        <Empty>
          No positions defined. Until there are, training compliance has no
          course to check anybody against: who has to take a course is set here,
          on the position.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {positions.data?.map((p) => (
            <li key={p.id}>
              <Card className="!p-0">
                <button type="button"
                        onClick={() => setOpen(open === p.id ? null : p.id)}
                        className="flex w-full items-center gap-2.5 p-3.5 text-left">
                  {open === p.id
                    ? <ChevronDown className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
                    : <ChevronRight className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{p.name}</span>
                    <span className="mt-0.5 block font-mono text-xs text-neutral-500">
                      {p.code}
                    </span>
                  </span>
                  <Counts position={p} />
                </button>

                {open === p.id && (
                  <div className="border-t border-neutral-100 p-3.5">
                    <RequirementsEditor position={p} mayWrite={mayWrite} />
                    <HoldersEditor position={p} mayWrite={mayWrite} />
                  </div>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Counts({ position }: { position: Position }) {
  const unfinished =
    (position.requirements > 0 && position.holders === 0) ||
    (position.holders > 0 && position.requirements === 0)
  return (
    <span className="flex shrink-0 items-center gap-2 text-xs">
      {unfinished && (
        <AlertTriangle className="h-4 w-4 text-warning-600" aria-hidden
                       aria-label="Half-defined" />
      )}
      <span className="text-neutral-600">
        {position.holders} {position.holders === 1 ? 'person' : 'people'}
      </span>
      <span className="text-neutral-400">·</span>
      <span className="text-neutral-600">
        {position.requirements} required
      </span>
    </span>
  )
}

function NewPositionForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/positions', { code, name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Add a position</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code">
          <input className={inputClass} value={code}
                 onChange={(e) => setCode(e.target.value.toUpperCase())}
                 placeholder="FORKLIFT" />
        </Field>
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Lift truck operator" />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!code.trim() || !name.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
    </Card>
  )
}

/**
 * Which courses this position requires.
 *
 * A checklist rather than an add-one-at-a-time list, because the question is
 * "which of these eight" and a checklist answers it in one screen. Courses
 * that already apply to everyone are shown ticked and disabled: requiring them
 * here again would be true but redundant, and letting somebody tick it implies
 * it does something.
 */
function RequirementsEditor(
  { position, mayWrite }: { position: Position; mayWrite: boolean },
) {
  const queryClient = useQueryClient()
  const [dirty, setDirty] = useState(false)
  const [chosen, setChosen] = useState<Set<string> | null>(null)

  const courses = useQuery({
    queryKey: ['courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })
  const requirements = useQuery({
    queryKey: ['position-requirements', position.id],
    queryFn: () => api.get<Requirement[]>(`/api/v1/positions/${position.id}/requirements`),
  })

  const current = chosen ?? new Set(
    (requirements.data ?? []).filter((r) => r.course_id).map((r) => r.course_id as string))

  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/positions/${position.id}/requirements`, {
      requirements: [...current].map((course_id) => ({
        requirement_type: 'training', course_id, is_mandatory: true,
      })),
    }),
    onSuccess: () => {
      setChosen(null); setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['position-requirements', position.id] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
    },
  })

  const toggle = (id: string) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id); else next.add(id)
    setChosen(next)
    setDirty(true)
  }

  return (
    <>
      <SectionTitle className="mb-1.5">Training this position requires</SectionTitle>
      <ul className="flex flex-col gap-1">
        {courses.data?.map((c) => (
          <li key={c.id}>
            <label className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm ${
              c.applies_to_all ? 'text-neutral-400' : 'text-neutral-800 hover:bg-neutral-50'}`}>
              <input type="checkbox"
                     checked={c.applies_to_all || current.has(c.id)}
                     disabled={!mayWrite || c.applies_to_all}
                     onChange={() => toggle(c.id)} />
              <span className="min-w-0 flex-1">{c.name}</span>
              {c.applies_to_all && (
                <span className="shrink-0 text-xs">Everyone takes this</span>
              )}
              {c.is_statutory && !c.applies_to_all && (
                <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                  Required by law
                </span>
              )}
            </label>
          </li>
        ))}
      </ul>
      <ErrorNote error={save.error} />
      {mayWrite && dirty && (
        <div className="mt-2 flex justify-end">
          <button type="button" className={primaryButton} disabled={save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save what this position needs'}
          </button>
        </div>
      )}
    </>
  )
}

function HoldersEditor({ position, mayWrite }: { position: Position; mayWrite: boolean }) {
  const queryClient = useQueryClient()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)

  const holders = useQuery({
    queryKey: ['position-holders', position.id],
    queryFn: () => api.get<Holder[]>(`/api/v1/positions/${position.id}/holders`),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['position-holders', position.id] })
    queryClient.invalidateQueries({ queryKey: ['positions'] })
    queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
  }

  const add = useMutation({
    mutationFn: (userId: string) =>
      api.post(`/api/v1/positions/${position.id}/holders`, { user_id: userId }),
    onSuccess: () => { setPerson(EMPTY_PERSON); invalidate() },
  })
  const remove = useMutation({
    mutationFn: (assignmentId: string) =>
      api.del(`/api/v1/positions/${position.id}/holders/${assignmentId}`),
    onSuccess: invalidate,
  })

  return (
    <div className="mt-4">
      <SectionTitle className="mb-1.5">Who holds it</SectionTitle>

      {holders.data?.length === 0 ? (
        <p className="text-sm text-neutral-500">Nobody yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {holders.data?.map((h) => (
            <li key={h.assignment_id}
                className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
              {h.user_name}
              <span className="text-neutral-400">since {formatDate(h.effective_from)}</span>
              {mayWrite && (
                <button type="button" aria-label={`Remove ${h.user_name}`}
                        disabled={remove.isPending}
                        onClick={() => { if (!remove.isPending) remove.mutate(h.assignment_id) }}
                        className="text-neutral-400 hover:text-danger-600">
                  <UserMinus className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {mayWrite && (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <div className="min-w-0 flex-1">
            <PersonPicker value={person} onChange={setPerson} allowExternal={false}
                          placeholder="Add somebody to this position" />
          </div>
          <button type="button" className={secondaryButton}
                  disabled={!person.userId || add.isPending}
                  onClick={() => { if (person.userId && !add.isPending) add.mutate(person.userId) }}>
            <Plus className="h-4 w-4" aria-hidden />
            Add
          </button>
        </div>
      )}
      <ErrorNote error={add.error || remove.error} />
      <p className="mt-1.5 text-xs text-neutral-500">
        Adding somebody here is what makes this position&rsquo;s training required of
        them, and what puts them in the compliance report when it lapses.
      </p>
    </div>
  )
}
