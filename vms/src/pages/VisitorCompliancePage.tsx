/** Per-visitor training + PPE compliance page (W11-S2-E follow-up).
 *
 * Lands here from:
 *   1. VMS Task Inbox row (vms_train / vms_ppe doc_type → /visitor/:id/compliance)
 *   2. The deeplink in the HR / Janitor notification email
 *
 * Only the configured contact (or system_admin) can click Confirm — the
 * server enforces the same gate and returns 403 otherwise.
 */
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, AlertCircle, CheckCircle2, ShieldCheck, HardHat, Loader2,
} from 'lucide-react'
import {
  isComplianceFresh, useConfirmPpe, useConfirmTraining, useVisitor,
} from '@/services/api'
import { formatDateTime } from '@/lib/utils'

export default function VisitorCompliancePage() {
  const { visitorId } = useParams<{ visitorId: string }>()
  const { data: visitor, isLoading, error } = useVisitor(visitorId)
  const confirmTraining = useConfirmTraining(visitorId ?? '')
  const confirmPpe      = useConfirmPpe(visitorId ?? '')

  if (isLoading) return <p className="text-sm text-neutral-400">Loading…</p>
  if (error || !visitor) {
    return (
      <div className="rounded-md border border-red-200 bg-danger-50 p-4 text-sm text-danger-600">
        <p className="flex items-center gap-2 font-medium">
          <AlertCircle className="h-4 w-4" />
          {error?.message ?? 'Visitor not found'}
        </p>
        <Link to="/tasks" className="mt-2 inline-flex items-center gap-1 text-xs text-danger-600 hover:underline">
          <ArrowLeft className="h-3 w-3" />
          Back to task inbox
        </Link>
      </div>
    )
  }

  const trainingFresh = isComplianceFresh(visitor.safety_training_confirmed_at)
  const ppeFresh = isComplianceFresh(visitor.ppe_issued_at)

  return (
    <div className="max-w-3xl">
      <Link to="/tasks" className="inline-flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700">
        <ArrowLeft className="h-3 w-3" />
        Back to task inbox
      </Link>

      <h1 className="mt-2 text-2xl font-bold text-neutral-900">
        {visitor.first_name} {visitor.last_name}
      </h1>
      <p className="mt-0.5 text-sm text-neutral-500">{visitor.company_name}</p>

      <div className="mt-6 space-y-4">
        <ComplianceCard
          icon={<ShieldCheck className="h-5 w-5" />}
          title="Food-safety training"
          who="HR training contact"
          fresh={trainingFresh}
          confirmedAt={visitor.safety_training_confirmed_at}
          isPending={confirmTraining.isPending}
          error={confirmTraining.error}
          onConfirm={() => confirmTraining.mutate()}
        />

        <ComplianceCard
          icon={<HardHat className="h-5 w-5" />}
          title="PPE issuance"
          who="Janitor PPE contact"
          fresh={ppeFresh}
          confirmedAt={visitor.ppe_issued_at}
          isPending={confirmPpe.isPending}
          error={confirmPpe.error}
          onConfirm={() => confirmPpe.mutate()}
        />
      </div>

      <p className="mt-6 text-xs text-neutral-400">
        Confirmations are valid for 12 months. Returning visitors within
        the window don't need to be re-confirmed at every visit.
      </p>
    </div>
  )
}

function ComplianceCard({
  icon, title, who, fresh, confirmedAt, isPending, error, onConfirm,
}: {
  icon: React.ReactNode
  title: string
  who: string
  fresh: boolean
  confirmedAt: string | null
  isPending: boolean
  error: Error | null
  onConfirm: () => void
}) {
  return (
    <div className={
      'rounded-lg border p-4 ' +
      (fresh ? 'border-emerald-200 bg-success-50/40' : 'border-amber-200 bg-amber-50/40')
    }>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
            {icon}
            {title}
          </p>
          <p className="mt-0.5 text-xs text-neutral-500">Confirms: {who}</p>

          {fresh && confirmedAt && (
            <p className="mt-2 flex items-center gap-1 text-xs text-success-600">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Confirmed {formatDateTime(confirmedAt)} (valid for 12 months)
            </p>
          )}
          {!fresh && confirmedAt && (
            <p className="mt-2 text-xs text-amber-700">
              Last confirmed {formatDateTime(confirmedAt)} — outside the 12-month window. Re-confirm to clear the gate.
            </p>
          )}
          {!fresh && !confirmedAt && (
            <p className="mt-2 text-xs text-amber-700">No confirmation on file yet.</p>
          )}
        </div>

        <button
          type="button"
          onClick={onConfirm}
          disabled={isPending || fresh}
          title={fresh ? 'Already fresh — no action needed' : undefined}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          {fresh ? 'Confirmed' : 'Confirm now'}
        </button>
      </div>

      {error && (
        <p className="mt-2 flex items-center gap-1 text-xs text-danger-600">
          <AlertCircle className="h-3 w-3" />
          {error.message}
        </p>
      )}
    </div>
  )
}
