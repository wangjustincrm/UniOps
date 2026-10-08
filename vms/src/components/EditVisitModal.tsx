import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  useAreaRules, useUpdateVisit,
  type AccessArea, type Visit, type VisitPurpose,
} from '@/services/api'
import { ACCESS_AREAS, PURPOSES, approvalTier } from '@/lib/visitOptions'

/** "HH:MM" of an ISO instant in the browser's (the plant's) time zone. */
function localTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/**
 * Edit an appointment before check-in: schedule, purpose, access area, notes.
 *
 * The access area is constrained the same way vms-api constrains it:
 *  - visit returned for edit / not yet submitted → any area (the next
 *    "Submit for approval" routes by the new area);
 *  - confirmed visit → only areas needing the same or less approval than it
 *    already has (e.g. GMP → Office after a failed health declaration).
 */
export function EditVisitModal({ visit, onClose }: { visit: Visit; onClose: () => void }) {
  const { data: rules } = useAreaRules()
  const update = useUpdateVisit(visit.id)

  const [visitDate, setVisitDate] = useState(visit.visit_date)
  const [arrival, setArrival] = useState(localTime(visit.planned_arrival))
  const [departure, setDeparture] = useState(localTime(visit.planned_departure))
  const [purpose, setPurpose] = useState<VisitPurpose>(visit.visit_purpose)
  const [area, setArea] = useState<AccessArea>(visit.access_area)
  const [notes, setNotes] = useState(visit.notes ?? '')

  const awaitingResubmit = visit.approval_status === 'draft' || visit.approval_status === 'returned'
  const currentTier = approvalTier(rules?.[visit.access_area])
  const inFlight = visit.approval_status === 'submitted' || visit.approval_status === 'in_review'
  const areaAllowed = (a: AccessArea) =>
    a === visit.access_area
    || (!inFlight && (awaitingResubmit || approvalTier(rules?.[a]) <= currentTier))

  const save = () => {
    update.mutate(
      {
        visit_date: visitDate,
        planned_arrival: new Date(`${visitDate}T${arrival}:00`).toISOString(),
        planned_departure: departure ? new Date(`${visitDate}T${departure}:00`).toISOString() : null,
        visit_purpose: purpose,
        ...(area !== visit.access_area ? { access_area: area } : {}),
        notes: notes.trim() || null,
      },
      { onSuccess: onClose },
    )
  }

  const inputCls = 'mt-1 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-neutral-900">Edit visit</h3>
        <p className="mt-1 text-xs text-neutral-500">
          {awaitingResubmit
            ? 'Make the changes the approver asked for, then use “Submit for approval”.'
            : 'Changes are logged. The access area can only move to an area that needs the same or less approval — for a higher-risk area, cancel and book a new visit.'}
        </p>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-xs font-medium text-neutral-600">Date
            <input type="date" value={visitDate} onChange={(e) => setVisitDate(e.target.value)} className={inputCls} />
          </label>
          <label className="text-xs font-medium text-neutral-600">Planned arrival
            <input type="time" value={arrival} onChange={(e) => setArrival(e.target.value)} className={inputCls} />
          </label>
          <label className="text-xs font-medium text-neutral-600">Planned departure
            <input type="time" value={departure} onChange={(e) => setDeparture(e.target.value)} className={inputCls} />
          </label>
        </div>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-neutral-600">Visit purpose
            <select value={purpose} onChange={(e) => setPurpose(e.target.value as VisitPurpose)} className={inputCls}>
              {PURPOSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-neutral-600">Access area
            <select value={area} onChange={(e) => setArea(e.target.value as AccessArea)} className={inputCls}>
              {ACCESS_AREAS.filter((o) => areaAllowed(o.value)).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
        </div>
        <label className="mt-3 block text-xs font-medium text-neutral-600">Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className={inputCls} />
        </label>

        {update.error && <p className="mt-3 text-xs text-danger-600">{update.error.message}</p>}

        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} disabled={update.isPending} className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={update.isPending || !visitDate || !arrival}
            className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {update.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Save changes
          </button>
        </div>
      </div>
    </div>
  )
}
