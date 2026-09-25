import { financeHandoffHref, vmsHandoffHref } from './api'

// ─── Task types ────────────────────────────────────────────────────────────────
//
// Source of truth for task-type display labels and navigation, shared by the Task
// Inbox page and the dashboard mini-inbox.
//
// TASK_TYPE_LABELS is the FULL catalog: it labels every task type the backend
// engine can emit (procurement lifecycle + GR steps + revisions + budget plan),
// so grouped inbox headers always render a clean label. Keep it in lockstep with
// the backend and with the wider `TaskType` union in `services/tasks.ts`.
//
// ALL_TASK_TYPES is the narrower legacy subset (the original 11 procurement types)
// used only where a curated ordered list is needed. It is NOT exhaustive — do not
// build a "show every type" control off it; iterate TASK_TYPE_LABELS keys instead.

export const ALL_TASK_TYPES = [
  'create_pr',
  'create_po',
  'create_pa',
  'create_prepayment_pa',
  'approve_pr',
  'approve_po',
  'approve_pa',
  'place_order',
  'confirm_settlement',
  'review_match',
  'match_invoice',
  'resolve_exception',
] as const

export type TaskType = (typeof ALL_TASK_TYPES)[number]

export const TASK_TYPE_LABELS: Record<string, string> = {
  create_pr: 'Create Purchase Request',
  create_po: 'Create Purchase Order',
  create_pa: 'Create Payment Application',
  create_prepayment_pa: 'Create Prepayment PA',
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  place_order: 'Place Order',
  confirm_settlement: 'Confirm Settlement',
  review_match: 'Confirm Invoice Match',
  match_invoice: 'Match Invoice to PO',
  resolve_exception: 'Resolve Match Exception',
  process_pa: 'Process Payment Application',
  approve_agr: 'Approve Agreement',
  revise_agr: 'Revise Agreement',
  confirm_period: 'Confirm Service Period',
  // 缺票逾期扫描签出的催票任务(app/tasks/agreement_overdue.py)
  chase_agreement_invoice: 'Chase Missing Invoice',
  // 发票驱动 / 完成日驱动的收货确认(api/v1/invoices.py、tasks/service_gr_due.py)
  // —— 一直是后端会发、这里却没有标签的类型,补上。
  confirm_receipt: 'Confirm Goods Receipt',
  // VMS 派发的任务(vms-api/app/services/visit_tasks.py)
  check_out_visitor: 'Check Out Visitor',
  prepare_ppe: 'Prepare PPE',
  revise_pr: 'Revise Purchase Request',
  revise_po: 'Revise Purchase Order',
  revise_pa: 'Revise Payment Application',
  acknowledge_gr: 'Acknowledge Goods Receipt',
  collect_goods: 'Collect Goods',
  confirm_service_gr: 'Confirm Service Receipt',
  gr_damage_report: 'Report Goods Damage',
  approve_budget_plan: 'Approve Budget Plan',
  revise_budget_plan: 'Revise Budget Plan',
  // PO sign-off (NC imports) — approval-api's posign action key.
  sign_po: 'Sign Purchase Order',
  revise_po_signoff: 'Revise PO Sign-off',
  // NC purchase sync could not import something (epms-api
  // services/nc_purchase_sync/error_tasks.py). Admin-only.
  import_erp_vendor: 'Import ERP Vendor',
  resolve_nc_sync_error: 'Resolve NC Sync Error',
  // Posted NC voucher lines whose dimensions keep them out of the Budget
  // Dashboard (finance-api services/jv_validation_tasks.py). Admin-only, one
  // standing task that closes itself when the findings clear.
  resolve_jv_validation: 'Resolve JV Validation Findings',
  // An invoice number NC has on more than one payable (finance-api
  // services/ap_duplicate_invoice_tasks.py). ap_clerk, one standing task that
  // closes itself when nothing unreviewed is left.
  resolve_ap_duplicate_invoice: 'Resolve Duplicate NC Invoices',
}

// Portal origin — NC sync errors that are not a missing vendor are resolved in
// Portal's Admin Panel (EPMS has no NC sync page of its own).
const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// ─── Navigation ────────────────────────────────────────────────────────────────

// document_type → detail-page route prefix.
const HREF_MAP: Record<string, string> = {
  pr: '/pr',
  po: '/po',
  gr: '/gr',
  invoice: '/invoices',
  pa: '/pa',
  agr: '/agreements',
  // PO sign-off tasks carry document_type 'posign' but anchor on the PO. Miss
  // this and the fallback below yields a bare "/<uuid>" — the same dead card
  // the vms_* branch above was added to fix.
  posign: '/po',
}

// A task carries a type plus the document it anchors on. Accept the minimal shape
// so this resolver works with the raw ApiTask as well as any mapped view model.
export interface TaskHrefInput {
  type: string
  document_type: string
  document_id: string
}

/**
 * Resolve the destination for a task card. Replaces the divergent per-surface
 * logic that previously lived in the inbox page and the dashboard.
 */
export function taskHref(task: TaskHrefInput): string {
  const docType = task.document_type.toLowerCase()

  // Budget Plans live in the Finance module — open them there via a full-page
  // handoff (absolute URL), not an in-app EPMS route.
  if (docType === 'budget_plan') {
    return financeHandoffHref(`/budget/plans/${task.document_id}`)
  }

  // VMS-owned docs. vms-api writes into the shared `tasks` table (visit
  // check-out / PPE prep from services/visit_tasks.py, training + PPE
  // compliance from services/compliance.py), so they reach this inbox — but
  // EPMS has no page for any of them. Without this branch HREF_MAP missed
  // every vms_* type and the fallback produced a bare "/<uuid>", i.e. a dead
  // link on the card. document_id is the VISITOR id for the two compliance
  // types (→ their compliance page) and the VISIT id for everything else,
  // matching Portal's vmsDeeplinkPath and VMS's own TaskInboxPage.
  if (docType === 'vms_train' || docType === 'vms_ppe') {
    return vmsHandoffHref(`/visitor/${task.document_id}/compliance`)
  }
  if (docType.startsWith('vms_')) {
    return vmsHandoffHref(`/${task.document_id}`)
  }

  // NC purchase-sync errors. Their subject is an NC order that is NOT in EPMS,
  // so there is no document to open — the destination is where the error gets
  // FIXED: the vendor registry for a missing ERP supplier, Portal's NC Purchase
  // Sync panel for everything else. document_id is the sync run, not a document.
  if (docType === 'nc_sync') {
    return task.type === 'import_erp_vendor'
      ? '/vendors'
      : `${PORTAL_URL}/admin?section=nc_sync`
  }

  // JV validation findings are voucher LINES, not a document — the destination
  // is the Finance page that lists them. document_id is the sync run that
  // reported them, so it cannot be part of the path.
  if (docType === 'jv_validation') {
    return financeHandoffHref('/finance/jv-validation')
  }
  // Same shape: the subject is a set of NC payables, document_id is the AP
  // sync run.
  if (docType === 'ap_duplicate_invoice') {
    return financeHandoffHref('/finance/ap-duplicate-invoices')
  }

  // Both create_pa and create_prepayment_pa anchor on the PO and open the PA
  // create page pre-filled from it (the /pa/create and /pa/new routes are the
  // same PaCreatePage).
  if (task.type === 'create_pa' || task.type === 'create_prepayment_pa') {
    return `/pa/create?poId=${task.document_id}`
  }

  return `${HREF_MAP[docType] ?? '/'}/${task.document_id}`
}
