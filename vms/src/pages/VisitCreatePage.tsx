import { useState } from 'react'
import { useReplaceTab } from '@uniops/shell'
import { vmsRoutes } from '@/app/routes'
import { AlertCircle, Loader2 } from 'lucide-react'
import { VisitorSearch } from '@/components/VisitorSearch'
import { HostSearch } from '@/components/HostSearch'
import { PpeRequestForm } from '@/components/PpeRequestForm'
import {
  useCreateVisit,
  useCreateVisitor,
  type AccessArea,
  type PpeRequest,
  type UserBrief,
  type Visitor,
  type VisitorType,
  type VisitPurpose,
} from '@/services/api'

/** Build a UserBrief for the currently logged-in user from the persisted
 * auth store (vms-auth) or the SSO handoff blob (portal-auth). Returns
 * null only when both are missing — the user shouldn't reach this page
 * unauthenticated. department_name isn't persisted client-side; HostSearch
 * tolerates null and falls back to the email line. */
function currentUserAsHost(): UserBrief | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const u = raw ? JSON.parse(raw)?.state?.user : null
      if (u?.id) {
        return {
          id: u.id,
          full_name: u.full_name ?? '',
          email: u.email ?? '',
          department_id: u.department_id ?? null,
          department_name: u.department_name ?? null,
        }
      }
    }
    return null
  } catch { return null }
}

const ACCESS_AREAS: { value: AccessArea; label: string }[] = [
  { value: 'office',             label: 'Office / Lobby' },
  { value: 'warehouse',          label: 'Warehouse' },
  { value: 'production_non_gmp', label: 'Production (Non-GMP)' },
  { value: 'production_gmp',     label: 'Production (GMP Clean Zone)' },
  { value: 'laboratory',         label: 'Laboratory' },
  { value: 'all',                label: 'Entire Plant' },
]

const PURPOSES: { value: VisitPurpose; label: string }[] = [
  { value: 'meeting',     label: 'Business Meeting' },
  { value: 'maintenance', label: 'Equipment Maintenance' },
  { value: 'tour',        label: 'Factory Tour' },
  { value: 'audit',       label: 'Audit / Inspection' },
  { value: 'interview',   label: 'Interview' },
  { value: 'delivery',    label: 'Delivery' },
  { value: 'other',       label: 'Other' },
]

const VISITOR_TYPES: { value: VisitorType; label: string }[] = [
  { value: 'supplier',    label: 'Supplier' },
  { value: 'contractor',  label: 'Contractor' },
  { value: 'inspector',   label: 'Regulatory Inspector' },
  { value: 'auditor',     label: 'Third-party Auditor' },
  { value: 'customer',    label: 'Customer / Partner' },
  { value: 'interviewee', label: 'Job Candidate' },
  { value: 'other',       label: 'Other' },
]

// ── New-visitor inline form ─────────────────────────────────────────────────-

function InlineNewVisitorForm({
  onCreated,
  onCancel,
}: {
  onCreated: (v: Visitor) => void
  onCancel: () => void
}) {
  const [first, setFirst] = useState('')
  const [last, setLast]   = useState('')
  const [company, setCo]  = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [type, setType]   = useState<VisitorType>('supplier')
  const { mutate, isPending, error } = useCreateVisitor()

  const submit = () => {
    mutate(
      {
        first_name: first.trim(),
        last_name:  last.trim(),
        company_name: company.trim(),
        phone: phone.trim() || undefined,
        email: email.trim() || undefined,
        visitor_type: type,
      } as Partial<Visitor>,
      { onSuccess: onCreated },
    )
  }

  const ok = first && last

  // Plain <div> — never <form>. This block is rendered inside the outer
  // New-Visit <form>, and HTML forbids nested forms (the inner one is
  // silently dropped, sending the Enter / Save click to the outer form).
  return (
    <div className="space-y-3 rounded-md border border-neutral-200 bg-white p-3">
      <p className="text-xs font-medium text-neutral-700">Register a new visitor</p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        <Field label="First name *">
          <input value={first} onChange={(e) => setFirst(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Last name *">
          <input value={last} onChange={(e) => setLast(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Company (optional)">
          <input value={company} onChange={(e) => setCo(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Phone (optional)">
          <input value={phone} onChange={(e) => setPhone(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Email (optional)">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Visitor type">
          <select value={type} onChange={(e) => setType(e.target.value as VisitorType)} className={inputCls}>
            {VISITOR_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
      </div>
      {error && (
        <p className="flex items-center gap-1 text-xs text-danger-600">
          <AlertCircle className="h-3 w-3" />
          {error.message}
        </p>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs">
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!ok || isPending}
          className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Save visitor
        </button>
      </div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────-

export default function VisitCreatePage() {
  // Submit/cancel replace THIS tab in place (no second tab, no re-submittable form).
  const replaceTab = useReplaceTab(vmsRoutes)

  const [visitor, setVisitor] = useState<Visitor | null>(null)
  const [additionalVisitors, setAdditionalVisitors] = useState<Visitor[]>([])
  const [showNewVisitor, setShowNewVisitor] = useState(false)
  // Default Host to the logged-in user — common case is "I am hosting this
  // visitor." User can click "Change" to pick someone else.
  const [host, setHost] = useState<UserBrief | null>(() => currentUserAsHost())

  const [visitDate, setVisitDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [arrivalTime, setArrivalTime] = useState('09:00')
  const [departureTime, setDepartureTime] = useState('17:00')
  const [purpose, setPurpose] = useState<VisitPurpose>('meeting')
  const [area, setArea] = useState<AccessArea>('office')
  const [notes, setNotes] = useState('')
  const [ppeRequest, setPpeRequest] = useState<PpeRequest | null>(null)

  const { mutate, isPending, error } = useCreateVisit()

  const isReady = !!visitor && !!host && !!visitDate && !!arrivalTime

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!visitor || !host) return
    const arrivalIso = new Date(`${visitDate}T${arrivalTime}:00`).toISOString()
    const departureIso = departureTime
      ? new Date(`${visitDate}T${departureTime}:00`).toISOString()
      : null
    mutate(
      {
        visitor_id: visitor.id,
        additional_visitor_ids: additionalVisitors.map((v) => v.id),
        host_id: host.id,
        visit_date: visitDate,
        planned_arrival: arrivalIso,
        planned_departure: departureIso,
        visit_purpose: purpose,
        access_area: area,
        notes: notes.trim() || null,
        ppe_requested: ppeRequest,
      },
      { onSuccess: (visit) => replaceTab(`/${visit.id}`) },
    )
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-neutral-900">New Visit</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Pre-register a visitor — you'll print the badge (and check them in) when they arrive.
      </p>

      <form onSubmit={submit} className="mt-6 space-y-6 rounded-lg border border-neutral-200 bg-white p-5">
        {/* Visitor — primary + companions (each gets their own badge at print time) */}
        <Section title={additionalVisitors.length > 0 ? `Visitors (${additionalVisitors.length + 1})` : 'Visitor'}>
          {showNewVisitor
            ? <InlineNewVisitorForm
                onCreated={(v) => {
                  // Newly registered visitor slots into whichever spot is open.
                  if (!visitor) setVisitor(v)
                  else setAdditionalVisitors((curr) => [...curr, v])
                  setShowNewVisitor(false)
                }}
                onCancel={() => setShowNewVisitor(false)}
              />
            : <VisitorSearch
                primary={visitor}
                additional={additionalVisitors}
                onChange={(p, extras) => { setVisitor(p); setAdditionalVisitors(extras) }}
                onNew={() => setShowNewVisitor(true)}
              />}
        </Section>

        {/* Host */}
        <Section title="Host">
          <HostSearch selected={host} onSelect={setHost} />
        </Section>

        {/* Schedule */}
        <Section title="Schedule">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Field label="Date">
              <input type="date" value={visitDate} onChange={(e) => setVisitDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Planned arrival">
              <input type="time" value={arrivalTime} onChange={(e) => setArrivalTime(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Planned departure">
              <input type="time" value={departureTime} onChange={(e) => setDepartureTime(e.target.value)} className={inputCls} />
            </Field>
          </div>
        </Section>

        {/* Purpose + area */}
        <Section title="Purpose & access">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field label="Visit purpose">
              <select value={purpose} onChange={(e) => setPurpose(e.target.value as VisitPurpose)} className={inputCls}>
                {PURPOSES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Access area">
              <select value={area} onChange={(e) => setArea(e.target.value as AccessArea)} className={inputCls}>
                {ACCESS_AREAS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
          </div>
          {(area === 'production_gmp' || area === 'laboratory') && (
            <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              GMP / Lab access requires a health declaration and Quality Manager approval before
              the badge can print.
            </p>
          )}
        </Section>

        {/* PPE (host-driven opt-in) — one size group per visitor */}
        <Section title="PPE">
          <PpeRequestForm
            visitors={visitor ? [visitor, ...additionalVisitors] : []}
            value={ppeRequest}
            onChange={setPpeRequest}
          />
        </Section>

        {/* Notes */}
        <Section title="Notes">
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            className={inputCls + ' resize-y'}
            placeholder="Anything the front desk should know…"
          />
        </Section>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
            <AlertCircle className="h-4 w-4" />
            {error.message}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={() => replaceTab('/')}
            className="rounded-md border border-neutral-300 px-4 py-2 text-sm"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!isReady || isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Create visit
          </button>
        </div>
      </form>
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

const inputCls =
  'w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-neutral-700">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-neutral-500">{title}</h2>
      {children}
    </section>
  )
}
