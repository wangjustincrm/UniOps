/**
 * Return-to-work planning, on the incident that caused it.
 *
 * Three things read this and until now all three came out blank, because the
 * table had no write path: the WSIB Form 7 package asks for the modified
 * duties and whether they are at regular pay; HSE's lost-time rule turns on
 * whether modified duties exist; and the check-ins are the only evidence that
 * anybody looked again.
 *
 * "At regular pay" is a three-way choice, not a checkbox. HSE's own
 * classification depends on it, and "nobody has said" is a different answer
 * from "no" — a checkbox would quietly record a guess as a fact on a document
 * that goes to the WSIB.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarCheck, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, ErrorNote, Field, inputClass, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'

interface Plan {
  id: string; incident_id: string; worker_user_id: string | null; worker_name: string
  functional_abilities: Record<string, string>
  restrictions: string | null; modified_duties: string | null
  at_regular_pay: boolean | null
  start_date: string; expected_end_date: string | null; actual_end_date: string | null
  status: string; created_at: string
}
interface Checkin {
  id: string; checkin_date: string; notes: string | null
  by_name: string | null; created_at: string
}

const PAY_CHOICES = [
  { value: 'yes', label: 'At their regular pay' },
  { value: 'no', label: 'At reduced pay' },
  { value: 'unknown', label: 'Not established yet' },
]

function payBool(v: string): boolean | null {
  return v === 'yes' ? true : v === 'no' ? false : null
}

export function ReturnToWorkPanel({ incidentId }: { incidentId: string }) {
  const { can } = usePermissions()
  const maySee = can('ehs.incident.medical.read')
  const [creating, setCreating] = useState(false)

  const plans = useQuery({
    queryKey: ['rtw', incidentId],
    queryFn: () => api.get<Plan[]>(`/api/v1/incidents/${incidentId}/return-to-work`),
    enabled: Boolean(incidentId) && maySee,
    retry: false,
  })

  // Not merely hidden: without the medical key the endpoint refuses, and an
  // empty panel would suggest there is no plan rather than that you cannot
  // see it.
  if (!maySee) return null

  const rows = plans.data ?? []

  return (
    <section className="mt-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Return to work</SectionTitle>
        {rows.length === 0 && !creating && (
          <button type="button" className={secondaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Plan a return
          </button>
        )}
      </div>

      {creating && (
        <PlanForm incidentId={incidentId} onClose={() => setCreating(false)} />
      )}

      {rows.length === 0 && !creating && (
        <p className="rounded-xl border border-dashed border-neutral-300 p-4 text-sm text-neutral-500">
          No plan. One is needed if the worker is coming back on modified duties —
          the WSIB export asks what those duties are and whether they are at
          regular pay, and both come from here.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {rows.map((p) => <PlanCard key={p.id} plan={p} incidentId={incidentId} />)}
      </ul>
    </section>
  )
}

function PlanCard({ plan, incidentId }: { plan: Plan; incidentId: string }) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const open = plan.status === 'active'

  const checkins = useQuery({
    queryKey: ['rtw-checkins', plan.id],
    queryFn: () => api.get<Checkin[]>(
      `/api/v1/incidents/${incidentId}/return-to-work/${plan.id}/checkins`),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['rtw', incidentId] })
    queryClient.invalidateQueries({ queryKey: ['rtw-checkins', plan.id] })
  }

  const addCheckin = useMutation({
    mutationFn: () => api.post(
      `/api/v1/incidents/${incidentId}/return-to-work/${plan.id}/checkins`, { notes: note }),
    onSuccess: () => { setNote(''); invalidate() },
  })
  const close = useMutation({
    mutationFn: () => api.post(
      `/api/v1/incidents/${incidentId}/return-to-work/${plan.id}/close`,
      { outcome: 'completed' }),
    onSuccess: invalidate,
  })

  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-neutral-900">{plan.worker_name}</p>
            <p className="text-xs text-neutral-500">
              From {formatDate(plan.start_date)}
              {plan.expected_end_date && ` · expected back ${formatDate(plan.expected_end_date)}`}
              {plan.actual_end_date && ` · ended ${formatDate(plan.actual_end_date)}`}
            </p>
          </div>
          <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
            open ? 'bg-warning-50 text-warning-700' : 'bg-success-50 text-success-700'}`}>
            {open ? 'On modified duties' : 'Back on full duties'}
          </span>
        </div>

        {plan.modified_duties && (
          <p className="mt-2 text-sm text-neutral-700">
            <span className="font-medium">Doing:</span> {plan.modified_duties}
          </p>
        )}
        {plan.restrictions && (
          <p className="mt-1 text-sm text-neutral-700">
            <span className="font-medium">Cannot:</span> {plan.restrictions}
          </p>
        )}
        <p className="mt-1 text-xs text-neutral-600">
          {plan.at_regular_pay === null
            ? 'Whether this is at regular pay has not been established — HSE’s '
              + 'lost-time rule turns on it.'
            : plan.at_regular_pay
              ? 'At their regular pay, so this does not count as lost time.'
              : 'At reduced pay.'}
        </p>

        {/* ── Check-ins ─────────────────────────────────────────────────── */}
        {(checkins.data?.length ?? 0) > 0 && (
          <ul className="mt-3 flex flex-col gap-1 border-t border-neutral-100 pt-2">
            {checkins.data?.map((c) => (
              <li key={c.id} className="text-xs">
                <span className="font-medium text-neutral-700">
                  {formatDate(c.checkin_date)}
                </span>
                <span className="ml-1.5 text-neutral-600">{c.notes}</span>
                {c.by_name && <span className="ml-1.5 text-neutral-400">— {c.by_name}</span>}
              </li>
            ))}
          </ul>
        )}

        {open && (
          <>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <input className={inputClass} value={note}
                     onChange={(e) => setNote(e.target.value)}
                     placeholder="How are they getting on?" />
              <button type="button" className={secondaryButton}
                      disabled={!note.trim() || addCheckin.isPending}
                      onClick={() => { if (!addCheckin.isPending) addCheckin.mutate() }}>
                <CalendarCheck className="h-4 w-4" aria-hidden />
                Check in
              </button>
            </div>
            <div className="mt-2 flex justify-end">
              <button type="button" className={primaryButton} disabled={close.isPending}
                      onClick={() => { if (!close.isPending) close.mutate() }}>
                {close.isPending ? 'Closing…' : 'Back on full duties'}
              </button>
            </div>
          </>
        )}
        <ErrorNote error={addCheckin.error || close.error} />
      </Card>
    </li>
  )
}

function PlanForm({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [startDate, setStartDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [expectedEnd, setExpectedEnd] = useState('')
  const [duties, setDuties] = useState('')
  const [restrictions, setRestrictions] = useState('')
  const [pay, setPay] = useState('unknown')

  const create = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${incidentId}/return-to-work`, {
      start_date: startDate,
      expected_end_date: expectedEnd || null,
      modified_duties: duties || null,
      restrictions: restrictions || null,
      at_regular_pay: payBool(pay),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rtw', incidentId] })
      onClose()
    },
  })

  return (
    <Card className="mb-2">
      <h3 className="mb-3 text-sm font-semibold text-neutral-900">Plan a return</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Starting">
          <input type="date" className={inputClass} value={startDate}
                 onChange={(e) => setStartDate(e.target.value)} />
        </Field>
        <Field label="Expected back on full duties">
          <input type="date" className={inputClass} value={expectedEnd}
                 onChange={(e) => setExpectedEnd(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="What they will be doing">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={duties}
                      onChange={(e) => setDuties(e.target.value)}
                      placeholder="Light assembly, no lifting over 5 kg" />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="What they cannot do">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={restrictions}
                      onChange={(e) => setRestrictions(e.target.value)}
                      placeholder="No ladder work; no lifting above shoulder height" />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Pay"
                 hint="HSE&rsquo;s lost-time rule turns on this, so &ldquo;not established&rdquo; is a real answer rather than a default">
            <select className={inputClass} value={pay} onChange={(e) => setPay(e.target.value)}>
              {PAY_CHOICES.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </Field>
        </div>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton} disabled={create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create the plan'}
        </button>
      </div>
    </Card>
  )
}
