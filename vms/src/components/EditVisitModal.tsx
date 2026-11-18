import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  useAreaRules, useUpdateVisit,
  type AccessArea, type Visit, type VisitPurpose,
} from '@/services/api'
import { PURPOSES, mayMoveConfirmed, zoneOptions } from '@/lib/visitOptions'
import { plantWallClockToISO } from '@/lib/plantTime'
import { formatTime } from '@/lib/utils'

import { Input, Select, Textarea, Button, Modal } from '@uniops/shell'
/** "HH:MM" of an ISO instant on the plant's clock (company time zone). */
function localTime(iso: string | null): string {
  if (!iso) return ''
  const t = formatTime(iso)
  return t === '—' ? '' : t
}

/**
 * Edit an appointment before check-in: schedule, purpose, access area, notes.
 *
 * The access zone is constrained the same way vms-api constrains it:
 *  - visit returned for edit / not yet submitted → any active zone (the next
 *    "Submit for approval" routes by the new zone);
 *  - confirmed visit → only zones that need no approval, or whose managers
 *    already had this visit (e.g. GMP → Office after a failed health declaration).
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
  const inFlight = visit.approval_status === 'submitted' || visit.approval_status === 'in_review'
  const areaAllowed = (a: AccessArea) =>
    a === visit.access_area
    || (!inFlight && (awaitingResubmit || visit.status !== 'confirmed'
        || mayMoveConfirmed(visit, rules?.[a])))

  const save = () => {
    update.mutate(
      {
        visit_date: visitDate,
        planned_arrival: plantWallClockToISO(visitDate, arrival),
        planned_departure: departure ? plantWallClockToISO(visitDate, departure) : null,
        visit_purpose: purpose,
        ...(area !== visit.access_area ? { access_area: area } : {}),
        notes: notes.trim() || null,
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      busy={update.isPending}
      title="Edit visit"
      // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
      closeOnEscape={false}
      size="full"
      className="max-w-lg"
    >
      <div>
        <p className="mt-1 text-xs text-fg-muted">
          {awaitingResubmit
            ? 'Make the changes the approver asked for, then use “Submit for approval”.'
            : 'Changes are logged. The access zone can only move to a zone that needs no approval or is managed by the same zone managers — otherwise cancel and book a new visit.'}
        </p>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-xs font-medium text-fg-secondary">Date
            <Input type="date" value={visitDate} onChange={(e) => setVisitDate(e.target.value)} className="mt-1" />
          </label>
          <label className="text-xs font-medium text-fg-secondary">Planned arrival
            <Input type="time" value={arrival} onChange={(e) => setArrival(e.target.value)} className="mt-1" />
          </label>
          <label className="text-xs font-medium text-fg-secondary">Planned departure
            <Input type="time" value={departure} onChange={(e) => setDeparture(e.target.value)} className="mt-1" />
          </label>
        </div>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-fg-secondary">Visit purpose
            <Select value={purpose} onChange={(e) => setPurpose(e.target.value as VisitPurpose)} className="mt-1 w-full">
              {PURPOSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </label>
          <label className="text-xs font-medium text-fg-secondary">Access zone
            <Select value={area} onChange={(e) => setArea(e.target.value as AccessArea)} className="mt-1 w-full">
              {zoneOptions(rules, visit.access_area).filter((o) => areaAllowed(o.value)).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </label>
        </div>
        <label className="mt-3 block text-xs font-medium text-fg-secondary">Notes
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="mt-1" />
        </label>

        {update.error && <p className="mt-3 text-xs text-danger-fg">{update.error.message}</p>}

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={update.isPending}>
            Cancel
          </Button>
          <Button size="sm"
            onClick={save}
            disabled={update.isPending || !visitDate || !arrival}
           
          >
            {update.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Save changes
          </Button>
        </div>
      </div>
    </Modal>
  )
}
