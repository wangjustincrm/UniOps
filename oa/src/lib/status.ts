// Canonical document workflow statuses (expense claims + PAs).
// Keep raw status string literals OUT of pages — compare against these.
export const STATUS = {
  DRAFT: 'draft',
  SUBMITTED: 'submitted',
  IN_REVIEW: 'in_review',
  APPROVED: 'approved',
  RETURNED: 'returned',
  REJECTED: 'rejected',
  CANCELLED: 'cancelled',
  PAID: 'paid',          // terminal — expense claims
  PROCESSED: 'processed', // terminal — PAs (domain synonym of PAID)
} as const
export type DocStatus = (typeof STATUS)[keyof typeof STATUS]

// Workflow actions (mutation bodies / action modals).
export const ACTION = {
  SUBMIT: 'submit',
  APPROVE: 'approve',
  RETURN: 'return',
  REJECT: 'reject',
  PAY: 'pay',
  CANCEL: 'cancel',
  RECALL: 'recall',
} as const
export type WorkflowAction = (typeof ACTION)[keyof typeof ACTION]

// Document type tokens (routing / task doc_type).
export const DOC_TYPE = {
  EXP: 'exp', MIL: 'mil', TRV: 'trv', CFM: 'cfm', TRA: 'tra', PA: 'pa', PA_DIR: 'pa_dir',
} as const

// Editable = owner can still edit/resubmit.
export const EDITABLE_STATUSES: readonly string[] = [STATUS.DRAFT, STATUS.RETURNED]
// In an approval queue (approver may act).
export const IN_APPROVAL_STATUSES: readonly string[] = [STATUS.SUBMITTED, STATUS.IN_REVIEW]
// Fully settled (paid OR processed) — resolves the paid/processed drift.
export const TERMINAL_STATUSES: readonly string[] = [STATUS.PAID, STATUS.PROCESSED]

export function isEditable(status: string): boolean { return EDITABLE_STATUSES.includes(status) }
export function isInApproval(status: string): boolean { return IN_APPROVAL_STATUSES.includes(status) }
export function isTerminal(status: string): boolean { return TERMINAL_STATUSES.includes(status) }
