import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import {
  AlertCircle, ArrowLeft, Calendar, Check, Clock, Eye, HardHat, Loader2, LogOut, Pencil, Printer, RotateCw, Send, ShieldAlert, ShieldCheck, ThumbsDown, ThumbsUp, Undo2, Users, X,
} from 'lucide-react'
import {
  useVisit, useVisitor, useUserBrief, useCancelVisit, useVerifyVisitorId,
  useBadgeHistory, useCheckOutVisit, useHealthDeclarations,
  useMyVmsTasks, useVisitAction, isComplianceFresh, useAreaRules, useSubmitVisit,
  type Visitor, type VisitApprovalAction, type HealthDeclaration,
} from '@/services/api'
import { StatusBadge, AccessAreaBadge, OverdueBadge, isVisitOverdue } from '@/components/StatusBadge'
import { CheckOutConfirm } from '@/components/CheckOutConfirm'
import { HealthDeclForm } from '@/components/HealthDeclForm'
import { HealthDeclView } from '@/components/HealthDeclView'
import { VisitAttachments } from '@/components/VisitAttachments'
import { EditVisitModal } from '@/components/EditVisitModal'
import { formatDateTime } from '@/lib/utils'

import { Button, Checkbox, Modal, Textarea, Badge, cardClass } from '@uniops/shell'
import { cn } from '@/lib/utils'
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

export default function VisitDetailPage() {
  const { visitId } = useParams<{ visitId: string }>()
  const navigate = useNavigate()

  const { data: visit, isLoading, error } = useVisit(visitId)
  const { data: visitor }       = useVisitor(visit?.visitor_id)
  useDocTabTitle(visitor ? `Visit ${visitor.first_name} ${visitor.last_name}` : undefined)
  const { data: host }          = useUserBrief(visit?.host_id)
  const { data: prints }        = useBadgeHistory(visitId)
  const { data: healthDecls }   = useHealthDeclarations(visitId)
  const { data: myTasks }       = useMyVmsTasks()
  const cancel                  = useCancelVisit(visitId)
  const verifyId                = useVerifyVisitorId()
  const checkOut                = useCheckOutVisit(visitId)
  const action                  = useVisitAction(visitId ?? '')
  const submit                  = useSubmitVisit(visitId)
  const { data: areaRules }     = useAreaRules()
  const [showEdit, setShowEdit] = useState(false)

  // Am I the assigned approver for this visit? Only an open *approve* task
  // counts — the requester's "Revise" task after a return is also a
  // vms_visit task on this visit, and used to show them Approve / Reject
  // buttons that could only fail.
  const myTaskForThisVisit = myTasks?.find(
    (t) => t.document_id === visitId && t.type === 'approve_vms_visit',
  )

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
    return <p className="text-sm text-fg-muted">Loading…</p>
  }

  if (error || !visit) {
    return (
      <div className="rounded-md border border-danger-line bg-danger-soft p-4 text-sm text-danger-fg">
        <p className="flex items-center gap-2 font-medium">
          <AlertCircle className="h-4 w-4" />
          {error?.message ?? 'Visit not found'}
        </p>
        <BackLink to="/" className="mt-2 inline-flex items-center gap-1 text-xs text-danger-fg hover:underline">
          <ArrowLeft className="h-3 w-3" />
          Back to today's visits
        </BackLink>
      </div>
    )
  }

  const isCancellable = visit.status === 'confirmed' || visit.status === 'pending_approval'
  const isPendingApproval = visit.status === 'pending_approval'
  const isFirstPrint = visit.actual_arrival === null
  const isCheckedIn = visit.status === 'checked_in'
  const isClosed = visit.status === 'cancelled' || visit.status === 'checked_out' || visit.status === 'no_show'
  const hasVisitorId = !!visitor

  // Zone rules come from vms-api (the rules it enforces): GMP-grade zones
  // (VMS Admin → Zones) need a passing health declaration for every visitor.
  const rule = areaRules?.[visit.access_area]
  const isGmpZone = !!rule?.requires_health_declaration
  const showComplianceGate = !!rule?.compliance_tasks_at_check_in
  const healthPassed = visit.health_decl_status === 'passed'
  const healthRequired = isGmpZone && !healthPassed   // any state other than `passed` blocks GMP print
  const canManage = !!visit.can_manage
  const awaitingSubmit = isPendingApproval && visit.approval_status === 'draft'
  const returnedForEdit = isPendingApproval && visit.approval_status === 'returned'
  const canEdit = canManage && !isClosed && !isCheckedIn

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
  // Say what is actually missing — the hint used to read "Verify visitor ID
  // first" even when the blocker was a health declaration.
  const printBlockers = [
    !allIdVerified && 'tick “ID Verified” for every visitor',
    isGmpZone && visit.health_decl_status === 'failed' && 'a health declaration failed — use “Edit visit” to move the visit to a non-GMP zone (e.g. Office), or cancel it',
    isGmpZone && visit.health_decl_status !== 'failed' && !healthPassed && 'every visitor needs a passing health declaration',
  ].filter(Boolean) as string[]

  const onCancel = () => {
    if (!confirm('Cancel this visit? Its approval tasks are closed and the Host is notified if it is not you.')) return
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
      <BackLink to="/" className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-secondary">
        <ArrowLeft className="h-3 w-3" />
        Back
      </BackLink>

      <div className="mt-2 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg">
            {visitor ? `${visitor.first_name} ${visitor.last_name}` : `Visit #${visit.id.slice(0, 8)}`}
            {visit.additional_visitors.length > 0 && (
              <span className="ml-2 text-sm font-medium text-fg-muted">
                + {visit.additional_visitors.length} {visit.additional_visitors.length === 1 ? 'companion' : 'companions'}
              </span>
            )}
          </h1>
          {visitor && (
            <p className="mt-0.5 text-sm text-fg-muted">{visitor.company_name}</p>
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
                <p className="text-xs font-semibold text-fg-muted">
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
          <p className="pt-2 text-xs text-fg-muted">
            {visit.ppe_notified_at
              ? `Janitor notified ${formatDateTime(visit.ppe_notified_at)}.`
              : 'Janitor will be emailed once the visit is approved.'}
          </p>
        </Card>
      )}

      {visit.additional_visitors.length > 0 && (
        <Card title="Companions" icon={<Users className="h-4 w-4" />} className="mt-4">
          <ul className="divide-y divide-line-subtle text-sm">
            {visit.additional_visitors.map((v) => (
              <li key={v.id} className="flex flex-wrap justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="font-medium text-fg">{v.first_name} {v.last_name}</p>
                  <p className="text-xs text-fg-muted">{v.company_name}</p>
                </div>
                <Badge variant={v.id_verified ? 'success' : 'warning'}>
                  {v.id_verified ? 'ID verified' : 'ID not verified'}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {visit.notes && (
        <Card title="Notes" className="mt-4">
          <p className="whitespace-pre-line text-sm text-fg-secondary">{visit.notes}</p>
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
                <span className="text-fg-secondary">
                  {idx === 0 ? 'Original' : `Reprint #${idx}`}
                  {p.reprint_reason && (
                    <span className="ml-2 text-xs text-fg-muted">({p.reprint_reason})</span>
                  )}
                </span>
                <span className="text-xs text-fg-muted">{formatDateTime(p.printed_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Training + PPE compliance gate (GMP / Lab only) */}
      {!isClosed && !isPendingApproval && showComplianceGate && visitor && (
        <ComplianceGate primary={visitor} extras={visit.additional_visitors} />
      )}

      {/* Health declarations (PRD §2.2.2 / VMS-CI-010) — GMP / Lab only.
          Every visitor files their own; the badge prints only once all are
          cleared. Stays visible (view-only) after the visit closes so signed
          declarations remain auditable. */}
      {isGmpZone && !isPendingApproval && (
        <div className="mt-6 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-fg-muted">
              Health declarations
            </p>
            <span className="text-xs text-fg-muted">
              {gateVisitors.filter((v) => declByVisitor[v.id]?.result === 'passed').length} of{' '}
              {gateVisitors.length} cleared
            </span>
          </div>
          {gateVisitors.map((v) => {
            const decl = declByVisitor[v.id]
            const status = decl?.result
            const name = `${v.first_name} ${v.last_name}`
            const tone = status === 'passed'
              ? 'border-success-line bg-success-soft'
              : status === 'failed'
              ? 'border-danger-line bg-danger-soft'
              : 'border-warning-line bg-warning-soft'
            return (
              <div key={v.id} className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 ${tone}`}>
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-sm font-medium text-fg">
                    {status === 'passed'
                      ? <ShieldCheck className="h-4 w-4 text-success-fg" />
                      : <ShieldAlert className={`h-4 w-4 ${status === 'failed' ? 'text-danger-fg' : 'text-warning-fg'}`} />}
                    {name}
                  </p>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    {status === 'passed'
                      ? `Cleared${decl ? ` · ${formatDateTime(decl.updated_at)}` : ''}`
                      : status === 'failed'
                      ? 'Failed — cannot enter this area today'
                      : 'Not declared yet'}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {decl && (
                    <Button variant="secondary" size="xs"
                      onClick={() => setViewDecl({ name, decl })}
                     
                    >
                      <Eye className="h-3.5 w-3.5" /> View
                    </Button>
                  )}
                  {canDeclare && (
                    <Button
                      size="xs"
                      variant={status === undefined ? 'warning' : 'secondary'}
                      onClick={() => setDeclaringVisitor({ id: v.id, name })}
                    >
                      {status === undefined ? <ShieldCheck className="h-3.5 w-3.5" /> : <RotateCw className="h-3 w-3" />}
                      {status === undefined ? 'Declare' : 'Re-file'}
                    </Button>
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
            <p className="text-xs font-semibold text-fg-muted">
              ID verification
            </p>
            <span className="text-xs text-fg-muted">
              {gateVisitors.filter((v) => v.id_verified).length} of {gateVisitors.length} verified
            </span>
          </div>
          {gateVisitors.map((v) => {
            const name = `${v.first_name} ${v.last_name}`
            const pending = verifyId.isPending && verifyId.variables === v.id
            return v.id_verified ? (
              <p key={v.id} className="flex items-center gap-1.5 rounded-md border border-success-line bg-success-soft px-3 py-2 text-xs text-success-fg">
                <ShieldCheck className="h-4 w-4" />
                {name} — ID verified.
              </p>
            ) : (
              <label key={v.id} className="flex cursor-pointer items-start gap-2.5 rounded-md border border-warning-line bg-warning-soft px-3 py-2.5 text-sm text-warning-fg">
                <Checkbox
                 
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

      {/* Not yet with the approvers: the first hand-off failed. */}
      {awaitingSubmit && (
        <div className="mt-6 rounded-lg border border-warning-line bg-warning-soft p-4 text-sm text-warning-fg">
          <p className="font-semibold">Not sent for approval yet</p>
          <p className="mt-0.5 text-xs">
            The approval service did not accept this visit when it was created. It is saved, but nobody
            has been asked to approve it and the badge cannot print until it is submitted.
          </p>
          {canManage && (
            <Button size="sm"
              onClick={() => submit.mutate()}
              disabled={submit.isPending}
              className="mt-3"
            >
              {submit.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              Submit for approval
            </Button>
          )}
          {submit.error && <p className="mt-2 text-xs text-danger-fg">{submit.error.message}</p>}
        </div>
      )}

      {/* Returned for edit: show why, let the Host fix it and resubmit. */}
      {returnedForEdit && (
        <div className="mt-6 rounded-lg border border-warning-line bg-warning-soft p-4 text-sm text-warning-fg">
          <p className="flex items-center gap-1.5 font-semibold">
            <Undo2 className="h-4 w-4" />
            Returned for edit{visit.approval_note?.actor_name ? ` by ${visit.approval_note.actor_name}` : ''}
          </p>
          {visit.approval_note?.comment && (
            <p className="mt-1 whitespace-pre-line rounded border border-warning-line bg-surface px-3 py-2 text-sm text-fg">
              {visit.approval_note.comment}
            </p>
          )}
          {canManage ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="secondary" size="sm"
                onClick={() => setShowEdit(true)}
               
              >
                <Pencil className="h-4 w-4" /> Edit visit
              </Button>
              <Button size="sm"
                onClick={() => submit.mutate()}
                disabled={submit.isPending}
               
              >
                {submit.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Submit for approval
              </Button>
            </div>
          ) : (
            <p className="mt-1 text-xs">Waiting for the person who booked it, or the Host, to edit and resubmit.</p>
          )}
          {submit.error && <p className="mt-2 text-xs text-danger-fg">{submit.error.message}</p>}
        </div>
      )}

      {/* Rejected: the visit shows as Cancelled; say who rejected it and why. */}
      {visit.status === 'cancelled' && visit.approval_status === 'rejected' && visit.approval_note && (
        <div className="mt-6 rounded-lg border border-danger-line bg-danger-soft p-4 text-sm text-danger-fg">
          <p className="font-semibold">
            Rejected{visit.approval_note.actor_name ? ` by ${visit.approval_note.actor_name}` : ''}
          </p>
          {visit.approval_note.comment && (
            <p className="mt-1 whitespace-pre-line text-fg">{visit.approval_note.comment}</p>
          )}
        </div>
      )}

      {/* Approval panel — only when I'm the assigned approver. */}
      {isPendingApproval && myTaskForThisVisit && (
        <div className="mt-6 rounded-lg border border-primary-200 bg-primary-50/40 p-4">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-primary-700">
            <ShieldCheck className="h-4 w-4" />
            This visit needs your approval ({myTaskForThisVisit.assigned_role.replace(/_/g, ' ')})
          </p>
          <p className="mt-0.5 text-xs text-fg-secondary">
            Review the visitor + host + access area, then approve, return for
            edit, or reject.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="success" size="sm"
              onClick={() => { setApprovalKind('approve'); setApprovalComment('') }}
              disabled={action.isPending}
             
            >
              <ThumbsUp className="h-4 w-4" />
              Approve
            </Button>
            <Button variant="warning-outline" size="sm"
              onClick={() => { setApprovalKind('return'); setApprovalComment('') }}
              disabled={action.isPending}
             
            >
              <Undo2 className="h-4 w-4" />
              Return for edit
            </Button>
            <Button variant="danger-outline" size="sm"
              onClick={() => { setApprovalKind('reject'); setApprovalComment('') }}
              disabled={action.isPending}
             
            >
              <ThumbsDown className="h-4 w-4" />
              Reject
            </Button>
          </div>
          {action.error && (
            <p className="mt-2 text-xs text-danger-fg">{action.error.message}</p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="mt-6 flex flex-wrap items-center gap-3">
        {isPendingApproval && !myTaskForThisVisit && !awaitingSubmit && !returnedForEdit && (
          <p className="rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-sm text-warning-fg">
            Awaiting approval — badge cannot print yet.
          </p>
        )}

        {!isPendingApproval && !isClosed && isFirstPrint && (
          <Button size="md"
            onClick={() => goPrint()}
            disabled={!canPrint}
            title={canPrint ? undefined : `Before printing: ${printBlockers.join('; ')}`}
           
          >
            <Printer className="h-4 w-4" />
            Print badge &amp; check in
          </Button>
        )}

        {isCheckedIn && (
          <>
            <Button size="md"
              onClick={() => setShowCheckoutModal(true)}
             
            >
              <LogOut className="h-4 w-4" />
              Check out
            </Button>
            <Button variant="secondary" size="md"
              onClick={() => setShowReprintModal(true)}
             
            >
              <RotateCw className="h-4 w-4" />
              Reprint badge
            </Button>
          </>
        )}

        {canEdit && !returnedForEdit && (
          <Button variant="secondary" size="md"
            onClick={() => setShowEdit(true)}
           
          >
            <Pencil className="h-4 w-4" />
            Edit visit
          </Button>
        )}

        {isCancellable && canManage && (
          <Button variant="danger-outline" size="md"
            onClick={onCancel}
            disabled={cancel.isPending}
           
          >
            {cancel.isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <X className="h-4 w-4" />}
            Cancel visit
          </Button>
        )}
      </div>

      {!canPrint && !isPendingApproval && !isClosed && isFirstPrint && printBlockers.length > 0 && (
        <p className="mt-2 text-xs text-fg-muted">Before printing: {printBlockers.join('; ')}.</p>
      )}
      {cancel.error && <p className="mt-2 text-xs text-danger-fg">{cancel.error.message}</p>}

      {showEdit && <EditVisitModal visit={visit} onClose={() => setShowEdit(false)} />}

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
        <Modal
          open
          onClose={() => { setShowReprintModal(false); setReprintReason('') }}
          title="Reprint badge"
          // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
          closeOnEscape={false}
          size="full"
          className="max-w-md"
        >
          <div>
            <p className="mt-1 text-xs text-fg-muted">
              Reprints are logged. Tell us why a new badge is needed.
            </p>
            <Textarea
              value={reprintReason}
              onChange={(e) => setReprintReason(e.target.value)}
              rows={3}
              placeholder="Damaged badge / lost / info change / …"
              className="mt-3 w-full"
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" size="sm"
                onClick={() => { setShowReprintModal(false); setReprintReason('') }}
               
              >
                Cancel
              </Button>
              <Button size="sm"
                onClick={onReprintSubmit}
                disabled={!reprintReason.trim()}
               
              >
                Reprint
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Approval modal — comment is required for reject/return, optional for approve. */}
      {approvalKind && (
        <Modal
          open
          onClose={() => { setApprovalKind(null); setApprovalComment(''); action.reset() }}
          busy={action.isPending}
          title={approvalKind === 'approve' ? 'Approve visit' : approvalKind === 'return' ? 'Return for edit' : 'Reject visit'}
          // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
          closeOnEscape={false}
          size="full"
          className="max-w-md"
        >
          <div>
            <p className="mt-1 text-xs text-fg-muted">
              {approvalKind === 'approve'
                ? 'Optional comment — visible in the visit history.'
                : 'Tell the requester why so they can fix it.'}
            </p>
            <Textarea
              value={approvalComment}
              onChange={(e) => setApprovalComment(e.target.value)}
              rows={3}
              placeholder={
                approvalKind === 'approve'
                  ? 'Optional note…'
                  : 'Reason (required)…'
              }
              className="mt-3 w-full"
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" size="sm"
                onClick={() => { setApprovalKind(null); setApprovalComment(''); action.reset() }}
                disabled={action.isPending}
               
              >
                Cancel
              </Button>
              <Button
                size="sm"
                variant={approvalKind === 'approve' ? 'success' : approvalKind === 'reject' ? 'danger' : 'warning'}
                onClick={() => action.mutate(
                  { action: approvalKind, comment: approvalComment.trim() || undefined },
                  { onSuccess: () => { setApprovalKind(null); setApprovalComment('') } },
                )}
                disabled={
                  action.isPending
                  || (approvalKind !== 'approve' && !approvalComment.trim())
                }
              >
                {action.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                Confirm
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

// ── Bits ────────────────────────────────────────────────────────────────────-

function Card({
  title, icon, children, className,
}: { title?: string; icon?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn(cardClass({ inset: true }), 'p-4', className)}>
      {title && (
        <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg-muted">
          {icon}
          {title}
        </div>
      )}
      <div className="divide-y divide-line-subtle">{children}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1.5 text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="font-medium text-fg">{value}</span>
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
      <p className="mt-6 flex items-center gap-1.5 rounded-md border border-success-line bg-success-soft px-3 py-2 text-xs text-success-fg">
        <ShieldCheck className="h-4 w-4" />
        Training + PPE up to date for all visitors on this appointment.
      </p>
    )
  }

  return (
    <div className="mt-6 rounded-md border border-warning-line bg-warning-soft p-3 text-sm text-warning-fg">
      <p className="flex items-center gap-1.5 font-medium">
        <HardHat className="h-4 w-4" />
        Training / PPE confirmation required
      </p>
      <p className="mt-1 text-xs">
        GMP, Laboratory and Entire Plant access need a fresh training + PPE record (within 12
        months) for every visitor. Confirmations are done by HR (training)
        and Janitor (PPE) on the visitor's compliance page.
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {visitors.map((v) => {
          const tFresh = isComplianceFresh(v.safety_training_confirmed_at)
          const pFresh = isComplianceFresh(v.ppe_issued_at)
          if (tFresh && pFresh) {
            return (
              <li key={v.id} className="text-success-fg">
                <Check aria-hidden className="mr-1 inline h-3.5 w-3.5" />{v.first_name} {v.last_name} — training + PPE on file
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
              <span className="text-warning-fg">
                — pending: {[!tFresh && 'training', !pFresh && 'PPE'].filter(Boolean).join(' + ')}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
