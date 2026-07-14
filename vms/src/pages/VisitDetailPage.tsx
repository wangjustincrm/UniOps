import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, AlertCircle, Calendar, Clock, Printer, X, Loader2, RotateCw, LogOut,
  ShieldCheck, ShieldAlert, ThumbsUp, ThumbsDown, Undo2, Users, HardHat, Eye,
} from 'lucide-react'
import {
  useVisit, useVisitor, useUserBrief, useCancelVisit, useVerifyVisitorId,
  useBadgeHistory, useCheckOutVisit, useHealthDeclarations,
  useMyVmsTasks, useVisitAction, isComplianceFresh,
  type Visitor, type VisitApprovalAction, type HealthDeclaration,
} from '@/services/api'
import { StatusBadge, AccessAreaBadge, OverdueBadge, isVisitOverdue } from '@/components/StatusBadge'
import { CheckOutConfirm } from '@/components/CheckOutConfirm'
import { HealthDeclForm } from '@/components/HealthDeclForm'
import { HealthDeclView } from '@/components/HealthDeclView'
import { VisitAttachments } from '@/components/VisitAttachments'
import { formatDateTime } from '@/lib/utils'

function getRole(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const r = raw ? JSON.parse(raw)?.state?.user?.role : null
      if (r) return r
    }
    return null
  } catch { return null }
}

const GMP_AREAS = new Set(['production_gmp', 'laboratory'])

export default function VisitDetailPage() {
  const { visitId } = useParams<{ visitId: string }>()
  const navigate = useNavigate()

  const { data: visit, isLoading, error } = useVisit(visitId)
  const { data: visitor }       = useVisitor(visit?.visitor_id)
  const { data: host }          = useUserBrief(visit?.host_id)
  const { data: prints }        = useBadgeHistory(visitId)
  const { data: healthDecls }   = useHealthDeclarations(visitId)
  const { data: myTasks }       = useMyVmsTasks()
  const cancel                  = useCancelVisit(visitId)
  const verifyId                = useVerifyVisitorId()
  const checkOut                = useCheckOutVisit(visitId)
  const action                  = useVisitAction(visitId ?? '')

  // Am I the assigned approver for this visit? — yes iff epms-api's
  // `/tasks` for me includes a row with document_id == this visit.
  const myTaskForThisVisit = myTasks?.find((t) => t.document_id === visitId)

  // Approve / Reject / Return modal
  const [approvalKind, setApprovalKind] = useState<VisitApprovalAction | null>(null)
  const [approvalComment, setApprovalComment] = useState('')

  // Reprint flow (visit already checked in)
  const [reprintReason, setReprintReason] = useState<string>('')
  const [showReprintModal, setShowReprintModal] = useState(false)
  const [showCheckoutModal, setShowCheckoutModal] = useState(false)
  const [declaringVisitor, setDeclaringVisitor] = useState<{ id: string; name: string } | null>(null)
  const [viewDecl, setViewDecl] = useState<{ name: string; decl: HealthDeclaration } | null>(null)

  if (isLoading) {
    return <p className="text-sm text-neutral-400">Loading…</p>
  }

  if (error || !visit) {
    return (
      <div className="rounded-md border border-red-200 bg-danger-50 p-4 text-sm text-danger-600">
        <p className="flex items-center gap-2 font-medium">
          <AlertCircle className="h-4 w-4" />
          {error?.message ?? 'Visit not found'}
        </p>
        <Link to="/" className="mt-2 inline-flex items-center gap-1 text-xs text-danger-600 hover:underline">
          <ArrowLeft className="h-3 w-3" />
          Back to today's visits
        </Link>
      </div>
    )
  }

  const isCancellable = visit.status === 'confirmed' || visit.status === 'pending_approval'
  const isPendingApproval = visit.status === 'pending_approval'
  const isFirstPrint = visit.actual_arrival === null
  const isCheckedIn = visit.status === 'checked_in'
  const isClosed = visit.status === 'cancelled' || visit.status === 'checked_out' || visit.status === 'no_show'
  const hasVisitorId = !!visitor

  // GMP / Lab visits require a passing health declaration before badge can print.
  const isGmpZone = GMP_AREAS.has(visit.access_area)
  const healthPassed = visit.health_decl_status === 'passed'
  const healthRequired = isGmpZone && !healthPassed   // any state other than `passed` blocks GMP print

  // Every person on the visit (primary + companions). Both gates below are
  // per-visitor: ID verification and health declarations apply to each one.
  const gateVisitors = [visitor, ...visit.additional_visitors].filter(
    (v): v is Visitor => !!v,
  )
  const declByVisitor: Record<string, HealthDeclaration> = {}
  for (const d of healthDecls ?? []) declByVisitor[d.visitor_id] = d

  // Badge prints only when EVERY visitor's ID is verified (not just the primary).
  const allIdVerified = gateVisitors.length > 0 && gateVisitors.every((v) => v.id_verified)

  // Declarations can only be filed while the visit is still open for it
  // (confirmed, pre-arrival). After check-in / close, the list is view-only.
  const canDeclare = !isClosed && !isCheckedIn

  const canPrint = hasVisitorId && allIdVerified && !isPendingApproval && !isClosed && !healthRequired

  const onCancel = () => {
    if (!confirm('Cancel this visit? The Host will be notified.')) return
    cancel.mutate(undefined, { onSuccess: () => navigate('/') })
  }

  const goPrint = (reason?: string) => {
    const qs = reason ? `?reason=${encodeURIComponent(reason)}` : ''
    navigate(`/badge/${visit.id}${qs}`)
  }

  const onReprintSubmit = () => {
    const reason = reprintReason.trim()
    if (!reason) return
    setShowReprintModal(false)
    goPrint(reason)
  }

  return (
    <div className="max-w-3xl">
      <Link to="/" className="inline-flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700">
        <ArrowLeft className="h-3 w-3" />
        Back
      </Link>

      <div className="mt-2 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">
            {visitor ? `${visitor.first_name} ${visitor.last_name}` : `Visit #${visit.id.slice(0, 8)}`}
            {visit.additional_visitors.length > 0 && (
              <span className="ml-2 text-sm font-medium text-neutral-500">
                + {visit.additional_visitors.length} {visit.additional_visitors.length === 1 ? 'companion' : 'companions'}
              </span>
            )}
          </h1>
          {visitor && (
            <p className="mt-0.5 text-sm text-neutral-500">{visitor.company_name}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {isVisitOverdue(visit) && <OverdueBadge />}
          <StatusBadge status={visit.status} />
          <AccessAreaBadge area={visit.access_area} />
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Schedule" icon={<Calendar className="h-4 w-4" />}>
          <Row label="Planned arrival"   value={formatDateTime(visit.planned_arrival)} />
          <Row label="Planned departure" value={formatDateTime(visit.planned_departure)} />
          <Row label="Actual arrival"    value={formatDateTime(visit.actual_arrival) ?? '—'} />
          <Row label="Actual departure"  value={formatDateTime(visit.actual_departure) ?? '—'} />
        </Card>

        <Card title="Visit" icon={<Clock className="h-4 w-4" />}>
          <Row label="Purpose"  value={visit.visit_purpose.replace(/_/g, ' ')} />
          <Row label="Host"     value={host?.full_name ?? '—'} />
          <Row label="Health declaration" value={visit.health_decl_status?.replace(/_/g, ' ') ?? '—'} />
          <Row label="Vehicle plate" value={visit.vehicle_plate ?? '—'} />
        </Card>
      </div>

      {visit.ppe_requested && visit.ppe_requested.items.length > 0 && (
        <Card title="PPE requested by Host" icon={<HardHat className="h-4 w-4" />} className="mt-4">
          {visit.ppe_requested.items.map((item) => {
            const allVisitors = [visitor, ...visit.additional_visitors].filter(
              (v): v is NonNullable<typeof v> => v != null,
            )
            const matched = allVisitors.find((v) => v.id === item.visitor_id)
            const label = matched
              ? `${matched.first_name} ${matched.last_name}`
              : `Visitor ${item.visitor_id.slice(0, 8)}`
            const clothing =
              item.clothing_size === 'other'
                ? (item.clothing_size_other || 'other')
                : item.clothing_size
            const footwear =
              item.footwear === 'shoes'
                ? `Safety shoes (size ${
                    item.shoe_size === 'other'
                      ? (item.shoe_size_other || 'other')
                      : item.shoe_size ?? '—'
                  })`
                : 'Shoe covers'
            return (
              <div key={item.visitor_id} className="py-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
                  {label}
                </p>
                <Row label="Clothing size" value={clothing} />
                <Row label="Footwear" value={footwear} />
              </div>
            )
          })}
          {visit.ppe_requested.notes && (
            <Row label="Notes" value={visit.ppe_requested.notes} />
          )}
          <p className="pt-2 text-xs text-neutral-500">
            {visit.ppe_notified_at
              ? `Janitor notified ${formatDateTime(visit.ppe_notified_at)}.`
              : 'Janitor will be emailed once the visit is approved.'}
          </p>
        </Card>
      )}

      {visit.additional_visitors.length > 0 && (
        <Card title="Companions" icon={<Users className="h-4 w-4" />} className="mt-4">
          <ul className="divide-y divide-neutral-100 text-sm">
            {visit.additional_visitors.map((v) => (
              <li key={v.id} className="flex flex-wrap justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="font-medium text-neutral-900">{v.first_name} {v.last_name}</p>
                  <p className="text-xs text-neutral-500">{v.company_name}</p>
                </div>
                <span className={
                  'rounded px-2 py-0.5 text-xs ' +
                  (v.id_verified
                    ? 'bg-success-50 text-success-600'
                    : 'bg-amber-50 text-amber-700')
                }>
                  {v.id_verified ? 'ID verified' : 'ID not verified'}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {visit.notes && (
        <Card title="Notes" className="mt-4">
          <p className="whitespace-pre-line text-sm text-neutral-700">{visit.notes}</p>
        </Card>
      )}

      {/* Attachments (S2-E / PRD §6.5.6) */}
      <div className="mt-4">
        <VisitAttachments visitId={visit.id} readOnly={getRole() === 'auditor'} />
      </div>

      {/* Badge prints */}
      {prints && prints.length > 0 && (
        <Card title="Badge prints" className="mt-4">
          <ul className="space-y-1 text-sm">
            {prints.map((p, idx) => (
              <li key={p.id} className="flex flex-wrap justify-between gap-2 py-1.5">
                <span className="text-neutral-700">
                  {idx === 0 ? 'Original' : `Reprint #${idx}`}
                  {p.reprint_reason && (
                    <span className="ml-2 text-xs text-neutral-500">({p.reprint_reason})</span>
                  )}
                </span>
                <span className="text-xs text-neutral-500">{formatDateTime(p.printed_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Training + PPE compliance gate (GMP / Lab only) */}
      {!isClosed && !isPendingApproval && isGmpZone && visitor && (
        <ComplianceGate primary={visitor} extras={visit.additional_visitors} />
      )}

      {/* Health declarations (PRD §2.2.2 / VMS-CI-010) — GMP / Lab only.
          Every visitor files their own; the badge prints only once all are
          cleared. Stays visible (view-only) after the visit closes so signed
          declarations remain auditable. */}
      {isGmpZone && !isPendingApproval && (
        <div className="mt-6 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
              Health declarations
            </p>
            <span className="text-xs text-neutral-500">
              {gateVisitors.filter((v) => declByVisitor[v.id]?.result === 'passed').length} of{' '}
              {gateVisitors.length} cleared
            </span>
          </div>
          {gateVisitors.map((v) => {
            const decl = declByVisitor[v.id]
            const status = decl?.result
            const name = `${v.first_name} ${v.last_name}`
            const tone = status === 'passed'
              ? 'border-emerald-200 bg-success-50'
              : status === 'failed'
              ? 'border-red-200 bg-danger-50'
              : 'border-amber-200 bg-amber-50'
            return (
              <div key={v.id} className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 ${tone}`}>
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-sm font-medium text-neutral-800">
                    {status === 'passed'
                      ? <ShieldCheck className="h-4 w-4 text-success-600" />
                      : <ShieldAlert className={`h-4 w-4 ${status === 'failed' ? 'text-danger-600' : 'text-amber-600'}`} />}
                    {name}
                  </p>
                  <p className="mt-0.5 text-xs text-neutral-500">
                    {status === 'passed'
                      ? `Cleared${decl ? ` · ${formatDateTime(decl.updated_at)}` : ''}`
                      : status === 'failed'
                      ? 'Failed — cannot enter GMP / Lab today'
                      : 'Not declared yet'}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {decl && (
                    <button
                      onClick={() => setViewDecl({ name, decl })}
                      className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
                    >
                      <Eye className="h-3.5 w-3.5" /> View
                    </button>
                  )}
                  {canDeclare && (
                    <button
                      onClick={() => setDeclaringVisitor({ id: v.id, name })}
                      className={
                        'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium ' +
                        (status === undefined
                          ? 'bg-amber-600 text-white hover:bg-amber-700'
                          : 'border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50')
                      }
                    >
                      {status === undefined ? <ShieldCheck className="h-3.5 w-3.5" /> : <RotateCw className="h-3 w-3" />}
                      {status === undefined ? 'Declare' : 'Re-file'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ID verification gate (PRD VMS-CI-005) — per visitor. Each person on a
          multi-visitor visit must have their photo ID checked. */}
      {!isClosed && !isPendingApproval && gateVisitors.length > 0 && (
        <div className="mt-6 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
              ID verification
            </p>
            <span className="text-xs text-neutral-500">
              {gateVisitors.filter((v) => v.id_verified).length} of {gateVisitors.length} verified
            </span>
          </div>
          {gateVisitors.map((v) => {
            const name = `${v.first_name} ${v.last_name}`
            const pending = verifyId.isPending && verifyId.variables === v.id
            return v.id_verified ? (
              <p key={v.id} className="flex items-center gap-1.5 rounded-md border border-emerald-200 bg-success-50 px-3 py-2 text-xs text-success-600">
                <ShieldCheck className="h-4 w-4" />
                {name} — ID verified.
              </p>
            ) : (
              <label key={v.id} className="flex cursor-pointer items-start gap-2.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
                <input
                  type="checkbox"
                  checked={false}
                  disabled={pending}
                  onChange={() => verifyId.mutate(v.id)}
                  className="mt-0.5"
                />
                <div className="leading-tight">
                  <p className="font-medium">ID Verified — {name}</p>
                  <p className="text-xs">
                    Confirm you have checked this visitor's photo ID (driver's licence,
                    passport, etc.). Required before the badge can print (PRD VMS-CI-005).
                  </p>
                </div>
                {pending && <Loader2 className="ml-auto h-4 w-4 animate-spin" />}
              </label>
            )
          })}
        </div>
      )}

      {/* Approval panel — only when I'm the assigned approver. */}
      {isPendingApproval && myTaskForThisVisit && (
        <div className="mt-6 rounded-lg border border-primary-200 bg-primary-50/40 p-4">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-primary-700">
            <ShieldCheck className="h-4 w-4" />
            This visit needs your approval ({myTaskForThisVisit.assigned_role.replace(/_/g, ' ')})
          </p>
          <p className="mt-0.5 text-xs text-neutral-600">
            Review the visitor + host + access area, then approve, return for
            edit, or reject.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => { setApprovalKind('approve'); setApprovalComment('') }}
              disabled={action.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-success-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-success-700 disabled:opacity-50"
            >
              <ThumbsUp className="h-4 w-4" />
              Approve
            </button>
            <button
              onClick={() => { setApprovalKind('return'); setApprovalComment('') }}
              disabled={action.isPending}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50"
            >
              <Undo2 className="h-4 w-4" />
              Return for edit
            </button>
            <button
              onClick={() => { setApprovalKind('reject'); setApprovalComment('') }}
              disabled={action.isPending}
              className="inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-danger-600 hover:bg-danger-50 disabled:opacity-50"
            >
              <ThumbsDown className="h-4 w-4" />
              Reject
            </button>
          </div>
          {action.error && (
            <p className="mt-2 text-xs text-danger-600">{action.error.message}</p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="mt-6 flex flex-wrap items-center gap-3">
        {isPendingApproval && !myTaskForThisVisit && (
          <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
            Awaiting approval — badge cannot print yet.
          </p>
        )}

        {!isPendingApproval && !isClosed && isFirstPrint && (
          <button
            onClick={() => goPrint()}
            disabled={!canPrint}
            title={canPrint ? undefined : 'Verify visitor ID first'}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            <Printer className="h-4 w-4" />
            Print badge &amp; check in
          </button>
        )}

        {isCheckedIn && (
          <>
            <button
              onClick={() => setShowCheckoutModal(true)}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
            >
              <LogOut className="h-4 w-4" />
              Check out
            </button>
            <button
              onClick={() => setShowReprintModal(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
            >
              <RotateCw className="h-4 w-4" />
              Reprint badge
            </button>
          </>
        )}

        {isCancellable && (
          <button
            onClick={onCancel}
            disabled={cancel.isPending}
            className="inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-4 py-2 text-sm text-danger-600 hover:bg-danger-50 disabled:opacity-50"
          >
            {cancel.isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <X className="h-4 w-4" />}
            Cancel visit
          </button>
        )}
      </div>

      {/* Health declaration modal */}
      {declaringVisitor && (
        <HealthDeclForm
          visitId={visit.id}
          visitorId={declaringVisitor.id}
          visitorName={declaringVisitor.name}
          onClose={() => setDeclaringVisitor(null)}
        />
      )}

      {viewDecl && (
        <HealthDeclView
          visitorName={viewDecl.name}
          result={viewDecl.decl.result}
          answers={viewDecl.decl.questionnaire_data.answers}
          signature={viewDecl.decl.signature}
          submittedAt={viewDecl.decl.updated_at}
          onClose={() => setViewDecl(null)}
        />
      )}

      {/* Check-out modal */}
      {showCheckoutModal && (
        <CheckOutConfirm
          visit={visit}
          visitor={visitor}
          host={host}
          isPending={checkOut.isPending}
          error={checkOut.error}
          onCancel={() => { setShowCheckoutModal(false); checkOut.reset() }}
          onConfirm={(payload) => checkOut.mutate(payload, {
            onSuccess: () => setShowCheckoutModal(false),
          })}
        />
      )}

      {/* Reprint modal */}
      {showReprintModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h3 className="text-base font-semibold text-neutral-900">Reprint badge</h3>
            <p className="mt-1 text-xs text-neutral-500">
              Reprints are logged. Tell us why a new badge is needed.
            </p>
            <textarea
              value={reprintReason}
              onChange={(e) => setReprintReason(e.target.value)}
              rows={3}
              placeholder="Damaged badge / lost / info change / …"
              className="mt-3 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => { setShowReprintModal(false); setReprintReason('') }}
                className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={onReprintSubmit}
                disabled={!reprintReason.trim()}
                className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                Reprint
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Approval modal — comment is required for reject/return, optional for approve. */}
      {approvalKind && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h3 className="text-base font-semibold text-neutral-900">
              {approvalKind === 'approve' && 'Approve visit'}
              {approvalKind === 'return'  && 'Return for edit'}
              {approvalKind === 'reject'  && 'Reject visit'}
            </h3>
            <p className="mt-1 text-xs text-neutral-500">
              {approvalKind === 'approve'
                ? 'Optional comment — visible in the visit history.'
                : 'Tell the requester why so they can fix it.'}
            </p>
            <textarea
              value={approvalComment}
              onChange={(e) => setApprovalComment(e.target.value)}
              rows={3}
              placeholder={
                approvalKind === 'approve'
                  ? 'Optional note…'
                  : 'Reason (required)…'
              }
              className="mt-3 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => { setApprovalKind(null); setApprovalComment(''); action.reset() }}
                disabled={action.isPending}
                className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => action.mutate(
                  { action: approvalKind, comment: approvalComment.trim() || undefined },
                  { onSuccess: () => { setApprovalKind(null); setApprovalComment('') } },
                )}
                disabled={
                  action.isPending
                  || (approvalKind !== 'approve' && !approvalComment.trim())
                }
                className={
                  'rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 ' +
                  (approvalKind === 'approve'
                    ? 'bg-success-600 hover:bg-success-700'
                    : approvalKind === 'reject'
                      ? 'bg-danger-600 hover:bg-danger-700'
                      : 'bg-amber-600 hover:bg-amber-700')
                }
              >
                {action.isPending && <Loader2 className="h-3 w-3 animate-spin inline mr-1" />}
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

function Card({
  title, icon, children, className,
}: { title?: string; icon?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={'rounded-lg border border-neutral-200 bg-white p-4 ' + (className ?? '')}>
      {title && (
        <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {icon}
          {title}
        </div>
      )}
      <div className="divide-y divide-neutral-100">{children}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1.5 text-sm">
      <span className="text-neutral-500">{label}</span>
      <span className="font-medium text-neutral-800">{value}</span>
    </div>
  )
}

function ComplianceGate({ primary, extras }: { primary: Visitor; extras: Visitor[] }) {
  const visitors = [primary, ...extras]
  // Surface a single status line per visitor; deep-link to their compliance
  // page if either gate is stale so HR / Janitor can clear it from here.
  const allClear = visitors.every(
    (v) => isComplianceFresh(v.safety_training_confirmed_at)
        && isComplianceFresh(v.ppe_issued_at),
  )

  if (allClear) {
    return (
      <p className="mt-6 flex items-center gap-1.5 rounded-md border border-emerald-200 bg-success-50 px-3 py-2 text-xs text-success-600">
        <ShieldCheck className="h-4 w-4" />
        Training + PPE up to date for all visitors on this appointment.
      </p>
    )
  }

  return (
    <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
      <p className="flex items-center gap-1.5 font-medium">
        <HardHat className="h-4 w-4" />
        Training / PPE confirmation required
      </p>
      <p className="mt-1 text-xs">
        GMP / Lab access needs a fresh training + PPE record (within 12
        months) for every visitor. Confirmations are done by HR (training)
        and Janitor (PPE) on the visitor's compliance page.
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {visitors.map((v) => {
          const tFresh = isComplianceFresh(v.safety_training_confirmed_at)
          const pFresh = isComplianceFresh(v.ppe_issued_at)
          if (tFresh && pFresh) {
            return (
              <li key={v.id} className="text-emerald-700">
                ✓ {v.first_name} {v.last_name} — training + PPE on file
              </li>
            )
          }
          return (
            <li key={v.id}>
              <Link
                to={`/visitor/${v.id}/compliance`}
                className="text-primary-700 hover:underline"
              >
                {v.first_name} {v.last_name}
              </Link>
              {' '}
              <span className="text-amber-700">
                — pending: {[!tFresh && 'training', !pFresh && 'PPE'].filter(Boolean).join(' + ')}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
