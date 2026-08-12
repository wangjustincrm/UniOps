export type UserRole =
  | 'requester'
  | 'dept_manager'
  | 'gm'
  | 'opm'
  | 'procurement_officer'
  | 'procurement_manager'
  | 'warehouse_staff'
  | 'ap_clerk'
  | 'finance_manager'
  | 'finance_bp'
  | 'cfo'
  | 'auditor'
  | 'vendor_manager'
  | 'erp_pa_officer'
  | 'system_admin'

export type DocumentStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'returned'
  | 'rejected'
  | 'cancelled'
  | 'issued'
  | 'partially_received'
  | 'fully_received'
  | 'confirmed'
  | 'collected'
  | 'matched'
  | 'paid'
  | 'closed'
  | 'nc_milk'
  // Purchase Agreement statuses (approval terminal state is 'active', not 'approved')
  | 'active'
  | 'expired'
  // Agreement payment-schedule row statuses (agreement_payment_schedule.status) —
  // distinct from the agreement's own DocumentStatus above. 'received' means an
  // invoice has been matched to the row, NOT that it's been human-confirmed —
  // confirmation is tracked separately via accepted_by/accepted_at and never
  // changes this status. 'waived' = excused from the schedule, no payment expected.
  | 'pending'
  | 'received'
  | 'overdue'
  | 'waived'
  // House-account pickup receipt statuses (agreement_receipts.status) —
  // 'rejected' above is shared (AP review can reject a receipt the same way a
  // PR/PO can be rejected). 'open' = posted, awaiting invoice match.
  // 'reconciled' = matched to an invoice. 'voided' = soft-cancelled.
  | 'pending_ap_review'
  | 'open'
  | 'reconciled'
  | 'voided'

export type ProcurementType = 1 | 2 | 3 | 4 | 5 | 6

// Flexible string type — accepts any currency code (ISO 4217 or custom)
export type Currency = string

export interface CurrencyDef {
  value: string
  label: string   // display name, e.g. "Canadian Dollar"
  symbol: string  // display symbol, e.g. "CA$"
}

/** Built-in preset currencies. Additional ones are stored in company settings. */
export const CURRENCIES: CurrencyDef[] = [
  { value: 'CAD', label: 'Canadian Dollar', symbol: 'CA$' },
  { value: 'USD', label: 'US Dollar',       symbol: 'US$' },
  { value: 'EUR', label: 'Euro',            symbol: '€'   },
  { value: 'CNY', label: 'Chinese Yuan',    symbol: '¥'   },
]

export interface User {
  id: string
  name: string
  email: string
  role: UserRole
  department_id: string | null
  department?: string
  avatar?: string
  teamsAccount?: string
  isActive?: boolean
}

export interface NavItem {
  label: string
  href: string
  icon: string
  badge?: number
  children?: NavItem[]
}

export interface TaskItem {
  id: string
  type: TaskType
  priority: 'urgent' | 'normal'
  title: string
  description: string
  documentId: string
  documentNumber: string
  dueDate?: string
  daysWaiting?: number
  amount?: number
  vendor?: string
  href: string
}

export type TaskType =
  | 'approve_pr'
  | 'approve_po'
  | 'approve_pa'
  | 'process_pa'
  | 'revise_pr'
  | 'revise_pa'
  | 'create_po'
  | 'place_order'
  | 'revise_po'
  | 'acknowledge_gr'
  | 'gr_damage_report'
  | 'collect_goods'
  | 'confirm_service_gr'
  | 'settle_prepayment'
  | 'link_invoice'
  | 'create_pa'

export interface PaLineItem {
  id: string
  poLineId: string      // FK to PoLineItem.id
  description: string   // copied from PO line
  qty: number           // qty being paid for in this PA
  unit: string
  unitPrice: number
  lineTotal: number     // qty × unitPrice
  notes?: string
}

export interface PrLineItem {
  id: string
  description: string
  materialId?: string        // per-line, shown for Types 1 & 3
  supplierItemId?: string    // e.g. Amazon ASIN, supplier catalog #
  qty: number
  unit: string
  unitPrice: number
  lineTotal: number          // computed: qty × unitPrice
  notes?: string
}

export const LINE_ITEM_UNITS = [
  'pcs', 'kg', 'set', 'pair', 'box', 'carton', 'roll', 'm', 'm²', 'L', 'hour', 'month', 'lot',
] as const

export interface ApprovalStep {
  id: string
  role: string
  actorName?: string
  status: 'completed' | 'current' | 'pending' | 'skipped'
  completedAt?: string
  channel?: 'Web' | 'Email' | 'Teams'
  action?: 'Approved' | 'Returned' | 'Rejected' | 'Created' | 'Issued'
  comment?: string
  daysWaiting?: number
}

export interface CurrentStep {
  role: string
  label: string
  approver_name: string | null
  since: string
}

export interface WorkflowNodeDef {
  id: string        // stable UUID
  label: string     // display label, e.g. "Department Manager"
  role: UserRole    // role that performs this approval step
}
