/**
 * Who is missing which required training.
 *
 * Usually the first record a Ministry inspector asks for, and the list the
 * next course session is booked from. Two questions, and they want opposite
 * shapes: "is this person clear to work" is per person, "who do I book a room
 * for" is per course. Both are here, on a toggle.
 *
 * By course is the default because of what this looks like in practice. The
 * plant has around two hundred people and eight required courses, so a flat
 * list of everyone short of something is not a list anybody reads — on the day
 * the module goes live it is every employee, four times over. Per course it is
 * eight rows, ordered by how far behind the plant is.
 *
 * Rendered as a panel inside the Training page's tabs rather than as a page of
 * its own, so it carries no heading and no padding — the page supplies both.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Search, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import {
  Empty, ErrorNote, inputClass, inputSized,
} from '@/components/ui'

interface Gap {
  user_id: string; user_name: string; course_id: string; course_code: string
  course_name: string; reason: string; expired_on: string | null
  department_name: string | null
}
interface CourseSummary {
  course_id: string; course_code: string; course_name: string
  required_of: number; missing: number; expired: number
}
interface Report {
  as_of: string; workers_considered: number; workers_compliant: number
  compliance_rate: number; gaps: Gap[]; courses: CourseSummary[]
}

type View = 'course' | 'person'
type Reason = 'all' | 'missing' | 'expired'

const NO_DEPARTMENT = '—'

export default function TrainingGapsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['training', 'gaps'],
    queryFn: () => api.get<Report>('/api/v1/training/gaps'),
  })

  const [view, setView] = useState<View>('course')
  const [query, setQuery] = useState('')
  const [course, setCourse] = useState('')
  const [department, setDepartment] = useState('')
  const [reason, setReason] = useState<Reason>('all')
  const [openCourse, setOpenCourse] = useState<string | null>(null)

  const gaps = data?.gaps ?? []

  // Departments come from the gap rows rather than from a lookup: this filter
  // exists to narrow what is on the screen, and a department with nobody
  // behind on anything would only ever select an empty list.
  const departments = useMemo(() => {
    const set = new Set<string>()
    for (const g of gaps) set.add(g.department_name || NO_DEPARTMENT)
    return [...set].sort((a, b) => (a === NO_DEPARTMENT ? 1 : b === NO_DEPARTMENT ? -1 : a.localeCompare(b)))
  }, [gaps])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return gaps.filter((g) => {
      if (course && g.course_id !== course) return false
      if (department && (g.department_name || NO_DEPARTMENT) !== department) return false
      if (reason !== 'all' && g.reason !== reason) return false
      if (!q) return true
      return (
        g.user_name.toLowerCase().includes(q)
        || g.course_name.toLowerCase().includes(q)
        || g.course_code.toLowerCase().includes(q)
        || (g.department_name ?? '').toLowerCase().includes(q)
      )
    })
  }, [gaps, query, course, department, reason])

  const filtersOn = Boolean(query || course || department || reason !== 'all')
  const clear = () => { setQuery(''); setCourse(''); setDepartment(''); setReason('all') }

  if (error) return <ErrorNote error={error} />
  if (isLoading || !data) return <p className="text-sm text-neutral-500">Loading…</p>

  const missingTotal = gaps.filter((g) => g.reason === 'missing').length
  const expiredTotal = gaps.length - missingTotal

  return (
    <div>
      <p className="mb-3 text-xs text-neutral-500">As of {formatDate(data.as_of)}</p>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
        <Stat label="Compliant" value={`${data.workers_compliant} of ${data.workers_considered}`} />
        <Stat label="Rate" value={`${Math.round(data.compliance_rate * 100)}%`}
              tone={data.compliance_rate < 0.9 ? 'warn' : 'ok'} />
        {/* Split, because the two need different action: one is a course to
            book, the other a certificate that lapsed on somebody already
            trained. Reported as one number they get worked the same way. */}
        <Stat label="Never taken" value={String(missingTotal)} tone={missingTotal ? 'bad' : 'ok'} />
        <Stat label="Expired" value={String(expiredTotal)} tone={expiredTotal ? 'warn' : 'ok'} />
      </div>

      {gaps.length === 0 ? (
        <p className="rounded-xl border border-success-500 bg-success-50 p-4 text-sm text-success-700">
          Everyone is up to date on their required training.
        </p>
      ) : (
        <>
          <div className="mb-3 flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-[13rem] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" aria-hidden />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search a name, department or course"
                  aria-label="Search"
                  className={`${inputClass} pl-8`}
                />
              </div>

              <select value={course} onChange={(e) => setCourse(e.target.value)}
                      aria-label="Course" className={inputSized('w-auto')}>
                <option value="">All courses</option>
                {data.courses.map((c) => (
                  <option key={c.course_id} value={c.course_id}>{c.course_name}</option>
                ))}
              </select>

              <select value={department} onChange={(e) => setDepartment(e.target.value)}
                      aria-label="Department" className={inputSized('w-auto')}>
                <option value="">All departments</option>
                {departments.map((d) => (
                  <option key={d} value={d}>{d === NO_DEPARTMENT ? 'No department' : d}</option>
                ))}
              </select>

              <Segmented
                value={reason} onChange={setReason}
                options={[
                  { key: 'all', label: 'All' },
                  { key: 'missing', label: 'Never taken' },
                  { key: 'expired', label: 'Expired' },
                ]}
              />

              <div className="flex-1" />

              <Segmented
                value={view} onChange={setView}
                options={[{ key: 'course', label: 'By course' }, { key: 'person', label: 'By person' }]}
              />
            </div>

            <p className="flex items-center gap-2 text-xs text-neutral-500">
              <span>
                {filtered.length} of {gaps.length} outstanding
                {filtersOn ? '' : ' — everything'}
              </span>
              {filtersOn && (
                <button onClick={clear} className="inline-flex items-center gap-1 text-primary-600 hover:underline">
                  <X className="h-3 w-3" aria-hidden />Clear filters
                </button>
              )}
            </p>
          </div>

          {filtered.length === 0 ? (
            <Empty>Nothing matches those filters.</Empty>
          ) : view === 'course' ? (
            <CourseView
              summaries={data.courses} rows={filtered}
              open={openCourse} onToggle={(id) => setOpenCourse((v) => (v === id ? null : id))}
            />
          ) : (
            <PersonView rows={filtered} />
          )}
        </>
      )}
    </div>
  )
}

// ── By course ────────────────────────────────────────────────────────────────

function CourseView({
  summaries, rows, open, onToggle,
}: {
  summaries: CourseSummary[]
  rows: Gap[]
  open: string | null
  onToggle: (courseId: string) => void
}) {
  const byCourse = new Map<string, Gap[]>()
  for (const g of rows) {
    const list = byCourse.get(g.course_id) ?? []
    list.push(g)
    byCourse.set(g.course_id, list)
  }

  // Summaries carry courses nobody is behind on; those are worth showing, at
  // nought outstanding. Filtered down, they are not — the count would be the
  // whole plant while the list underneath is empty.
  const shown = summaries.filter((c) => byCourse.has(c.course_id))

  return (
    <ul className="flex flex-col gap-2">
      {shown.map((c) => {
        const people = byCourse.get(c.course_id) ?? []
        const isOpen = open === c.course_id
        const done = Math.max(c.required_of - c.missing - c.expired, 0)
        const pct = c.required_of ? Math.round((done / c.required_of) * 100) : 100
        return (
          <li key={c.course_id} className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
            <button
              onClick={() => onToggle(c.course_id)}
              aria-expanded={isOpen}
              className="flex w-full items-center gap-3 p-3.5 text-left transition-colors hover:bg-neutral-50"
            >
              <ChevronRight
                className={`h-4 w-4 shrink-0 text-neutral-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-xs text-neutral-500">{c.course_code}</span>
                  <span className="text-sm font-semibold text-neutral-900">{c.course_name}</span>
                </p>
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-neutral-100">
                    <div className="h-full rounded-full bg-success-500" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-neutral-500">
                    {done}/{c.required_of} trained
                  </span>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-danger-50 px-2.5 py-1 font-mono text-xs font-semibold tabular-nums text-danger-700">
                {people.length}
              </span>
            </button>

            {isOpen && (
              <ul className="border-t border-neutral-100">
                {people.map((g) => (
                  <li key={g.user_id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-neutral-100 px-3.5 py-2 last:border-b-0">
                    <span className="text-sm text-neutral-800">{g.user_name}</span>
                    <span className="min-w-0 flex-1 truncate text-xs text-neutral-500">
                      {g.department_name || 'No department'}
                    </span>
                    <ReasonTag gap={g} />
                  </li>
                ))}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )
}

// ── By person ────────────────────────────────────────────────────────────────

function PersonView({ rows }: { rows: Gap[] }) {
  const byWorker = new Map<string, Gap[]>()
  for (const g of rows) {
    const list = byWorker.get(g.user_id) ?? []
    list.push(g)
    byWorker.set(g.user_id, list)
  }

  return (
    <ul className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
      {[...byWorker.entries()].map(([userId, gaps]) => (
        <li key={userId}
            className="flex flex-col gap-1.5 border-b border-neutral-100 p-3 last:border-b-0 sm:flex-row sm:items-start sm:gap-4">
          <div className="min-w-0 sm:w-56 sm:shrink-0">
            <p className="truncate text-sm font-semibold text-neutral-900">{gaps[0].user_name}</p>
            <p className="truncate text-xs text-neutral-500">
              {gaps[0].department_name || 'No department'}
            </p>
          </div>
          <ul className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {gaps.map((g) => (
              <li key={g.course_id}>
                <ReasonTag gap={g} withCourse />
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

// ── Bits ─────────────────────────────────────────────────────────────────────

function ReasonTag({ gap, withCourse = false }: { gap: Gap; withCourse?: boolean }) {
  const expired = gap.reason === 'expired'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] ${
      expired ? 'bg-warning-50 text-warning-700' : 'bg-danger-50 text-danger-700'}`}>
      {withCourse && <span className="font-mono text-[11px] opacity-80">{gap.course_code}</span>}
      <span className="font-semibold">
        {expired
          ? `Expired${gap.expired_on ? ` ${formatDate(gap.expired_on)}` : ''}`
          : 'Never taken'}
      </span>
    </span>
  )
}

function Segmented<T extends string>({
  value, onChange, options,
}: {
  value: T
  onChange: (v: T) => void
  options: { key: T; label: string }[]
}) {
  return (
    <div className="flex shrink-0 rounded-lg border border-neutral-200 bg-white p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
            value === o.key ? 'bg-primary-600 text-white' : 'text-neutral-600 hover:bg-neutral-50'}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' | 'bad' }) {
  const colour = tone === 'bad' ? 'text-danger-700'
    : tone === 'warn' ? 'text-warning-700'
    : tone === 'ok' ? 'text-success-700'
    : 'text-neutral-900'
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums ${colour}`}>{value}</p>
    </div>
  )
}
