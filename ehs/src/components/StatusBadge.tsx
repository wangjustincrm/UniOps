/**
 * Severity badges.
 *
 * The injury class and the Ministry-reportable flag are rendered as two
 * separate badges, never as one, because they are two independent facts: a
 * lost-time injury can also be reportable. The MOL badge is the only
 * solid-filled badge in the module, reserved for the one state that starts a
 * 48-hour legal clock.
 *
 * Colour is never the only signal — every badge carries its own words.
 */
const INJURY_LABELS: Record<string, { label: string; className: string }> = {
  first_aid: { label: 'First aid', className: 'bg-info-50 text-info-700' },
  medical_aid: { label: 'Medical aid', className: 'bg-warning-50 text-warning-700' },
  lost_time: { label: 'Lost time', className: 'bg-danger-50 text-danger-700' },
}

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  under_investigation: 'Investigating',
  pending_closure: 'Pending closure',
  closed: 'Closed',
  cancelled: 'Cancelled',
  open: 'Open',
  in_progress: 'In progress',
  pending_verification: 'Awaiting verification',
}

const base = 'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold'

export function InjuryBadge({ injuryClass }: { injuryClass: string | null }) {
  if (!injuryClass) return null
  const spec = INJURY_LABELS[injuryClass]
  if (!spec) return null
  return <span className={`${base} ${spec.className}`}>{spec.label}</span>
}

export function MolBadge({ reportable }: { reportable: boolean }) {
  if (!reportable) return null
  return <span className={`${base} bg-danger-600 text-white`}>MOL reportable</span>
}

export function StatusBadge({ status }: { status: string }) {
  const closed = status === 'closed'
  return (
    <span
      className={`${base} ${closed ? 'bg-success-50 text-success-700' : 'bg-neutral-100 text-neutral-600'}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}
