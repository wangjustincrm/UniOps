import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Currency, CurrencyDef, WorkflowNodeDef } from '@/types'

// ─── Config sub-types ─────────────────────────────────────────────────────────

export interface WorkflowConfig {
  escalationThresholdCAD: number        // GM/OPM escalation threshold, default 60000
  poLowValueBypassEnabled: boolean      // skip GM/OPM for low-value POs, default false
  poLowValueBypassCAD: number           // threshold when enabled, default 5000
  approvalReminderDays: number          // reminder before auto-escalate, default 2
  approvalAutoEscalationDays: number    // auto-escalate after N days, default 5
  overBudgetMode: 'fm_gm_opm' | 'fm_only' // who approves over-budget, default fm_gm_opm
  consolidateGmOpmApproval: boolean     // merge OB-2 + value escalation into one, default true
}

export interface ServiceGrSlaConfig {
  reminderDays: number            // Day 0 + N reminder to Requester, default 1
  managerEscalationDays: number   // escalate to Dept Manager, default 3
  gmOpmEscalationDays: number     // escalate to GM/OPM, default 5
  fmAlertDays: number             // alert Finance Manager, default 7
}

export interface GrNotificationSlaConfig {
  reminderDays: number            // acknowledgement reminder, default 1
  managerEscalationDays: number   // escalate non-ack to Dept Manager, default 3
}

export interface PrepaymentConfig {
  maxPrepaymentPct: number                     // 1–100, default 100
  settlementSlaDays: number                    // days after GR to settle, default 5
  settlementManagerEscalationDays: number      // after SLA breach, default 3
  settlementGmOpmEscalationDays: number        // after SLA breach, default 5
  blockPoClosureOnUnsettled: boolean           // default true
}

export interface BudgetAdminConfig {
  yellowThresholdPct: number      // default 80
  redThresholdPct: number         // default 100
  overBudgetMode: 'fm_gm_opm' | 'fm_only' | 'hard_block'
}

export interface CollectionConfig {
  collectionRequired: boolean     // global toggle, default true
  reminderDays: number            // Requester reminder, default 2
  managerEscalationDays: number   // Dept Manager escalation, default 4
  fmAlertDays: number             // Finance Manager alert, default 7
}

export interface TempAssignment {
  id: string
  delegateUserId: string
  roleKey: string         // e.g. 'gm', 'opm', 'dept_manager', 'finance_bp'
  startDate: string
  endDate: string
  createdAt: string
}

export interface RoleManagementConfig {
  gmUserId: string | null
  gmBackupUserId: string | null
  opmUserId: string | null
  opmBackupUserId: string | null
  financeBpUserIds: string[]
  tempAssignments: TempAssignment[]
}

// ─── Main settings interface ──────────────────────────────────────────────────

export interface CompanySettings {
  name: string
  tagline: string
  logoDataUrl: string | null
  logoFileName: string | null
  deliveryAddress: string
  defaultCurrency: Currency
  enabledCurrencies: Currency[]
  customCurrencies: CurrencyDef[]
  mfaEnabled: boolean
  passwordExpiryDays: number | null
  poEmailSubject: string
  poEmailBody: string
  pdfTemplates: {
    pr: PdfTemplateSettings
    po: PdfTemplateSettings
    gr: PdfTemplateSettings
    pa: PdfTemplateSettings
  }
  // ── New config sections ──
  workflowConfig: WorkflowConfig
  deptGmOpmMapping: Record<string, 'gm' | 'opm'>   // deptId -> scope
  deptSupervisorEnabled: Record<string, boolean>     // deptId -> enabled
  serviceGrSla: ServiceGrSlaConfig
  grNotificationSla: GrNotificationSlaConfig
  prepaymentConfig: PrepaymentConfig
  budgetAdminConfig: BudgetAdminConfig
  collectionConfig: CollectionConfig
  roleManagement: RoleManagementConfig
  workflowDefs: {
    pr: WorkflowNodeDef[]
    po: WorkflowNodeDef[]
    pa: WorkflowNodeDef[]
  }
}

export interface PdfTemplateSettings {
  showLogo: boolean
  headerNote: string
  footerNote: string
  showTerms: boolean
  termsText: string
}

interface CompanyStoreState {
  settings: CompanySettings
  updateSettings: (patch: Partial<CompanySettings>) => void
  clearLogo: () => void
  addTempAssignment: (a: Omit<TempAssignment, 'id' | 'createdAt'>) => void
  removeTempAssignment: (id: string) => void
}

const DEFAULTS: CompanySettings = {
  name: 'EPMS',
  tagline: 'Enterprise Procurement Management',
  logoDataUrl: null,
  logoFileName: null,
  deliveryAddress: '',
  defaultCurrency: 'CAD',
  enabledCurrencies: ['CAD', 'USD', 'EUR', 'RMB'],
  customCurrencies: [],
  mfaEnabled: true,
  passwordExpiryDays: 90,
  pdfTemplates: {
    pr: { showLogo: true, headerNote: '', footerNote: '', showTerms: false, termsText: 'This Purchase Requisition is a formal internal request and does not constitute a binding commitment to the vendor until a Purchase Order is issued. Please quote the PR number in all related correspondence.' },
    po: { showLogo: true, headerNote: '', footerNote: '', showTerms: true,  termsText: 'Payment terms: Net 30 days from invoice date. Please confirm receipt of this Purchase Order and advise of any issues with availability or delivery dates. Reference the PO number on all correspondence and invoices. This PO was issued via the company procurement system.' },
    gr: { showLogo: true, headerNote: '', footerNote: '', showTerms: false, termsText: '' },
    pa: { showLogo: true, headerNote: '', footerNote: '', showTerms: false, termsText: '' },
  },
  poEmailSubject: 'Purchase Order {{po_number}} — {{company_name}}',
  poEmailBody: `Dear {{vendor_name}},

Please find below our Purchase Order for your reference.

PO Number:          {{po_number}}
PO Date:            {{po_date}}
Expected Delivery:  {{expected_delivery}}
Delivery Address:   {{delivery_address}}

ITEMS ORDERED
{{line_items}}

Subtotal:   {{subtotal}}
Tax:        {{tax}}
Total:      {{total}}

Payment Terms: Net 30 days from invoice date.

Please confirm receipt of this order and advise of any issues with availability or delivery dates.

This PO was issued via {{company_name}} procurement system. Please reference the PO number on all correspondence and invoices.

Regards,
{{sender_name}}
{{company_name}}
`,
  workflowConfig: {
    escalationThresholdCAD: 60000,
    poLowValueBypassEnabled: false,
    poLowValueBypassCAD: 5000,
    approvalReminderDays: 2,
    approvalAutoEscalationDays: 5,
    overBudgetMode: 'fm_gm_opm',
    consolidateGmOpmApproval: true,
  },
  deptGmOpmMapping: {
    d1: 'gm',   // Marketing
    d2: 'gm',   // Finance
    d3: 'gm',   // Procurement
    d4: 'opm',  // Operations
    d5: 'gm',   // Executive
    d6: 'gm',   // IT
    d7: 'gm',   // HR
  },
  deptSupervisorEnabled: {},
  serviceGrSla: {
    reminderDays: 1,
    managerEscalationDays: 3,
    gmOpmEscalationDays: 5,
    fmAlertDays: 7,
  },
  grNotificationSla: {
    reminderDays: 1,
    managerEscalationDays: 3,
  },
  prepaymentConfig: {
    maxPrepaymentPct: 100,
    settlementSlaDays: 5,
    settlementManagerEscalationDays: 3,
    settlementGmOpmEscalationDays: 5,
    blockPoClosureOnUnsettled: true,
  },
  budgetAdminConfig: {
    yellowThresholdPct: 80,
    redThresholdPct: 100,
    overBudgetMode: 'fm_gm_opm',
  },
  collectionConfig: {
    collectionRequired: true,
    reminderDays: 2,
    managerEscalationDays: 4,
    fmAlertDays: 7,
  },
  roleManagement: {
    gmUserId: null,
    gmBackupUserId: null,
    opmUserId: null,
    opmBackupUserId: null,
    financeBpUserIds: [],
    tempAssignments: [],
  },
  workflowDefs: {
    pr: [
      { id: 'pr-n1', label: 'Department Manager', role: 'dept_manager' },
      { id: 'pr-n2', label: 'GM / OPM Approval', role: 'gm' },
      { id: 'pr-n3', label: 'Finance Manager', role: 'finance_manager' },
    ],
    po: [
      { id: 'po-n0', label: 'Procurement Manager', role: 'procurement_manager' },
      { id: 'po-n1', label: 'Finance Manager', role: 'finance_manager' },
    ],
    pa: [
      { id: 'pa-n1', label: 'Finance BP Review', role: 'finance_bp' },
      { id: 'pa-n2', label: 'Finance Manager Approval', role: 'finance_manager' },
    ],
  },
}

export const useCompanyStore = create<CompanyStoreState>()(
  persist(
    (set) => ({
      settings: DEFAULTS,

      updateSettings: (patch) =>
        set((s) => ({ settings: { ...s.settings, ...patch } })),

      clearLogo: () =>
        set((s) => ({
          settings: { ...s.settings, logoDataUrl: null, logoFileName: null },
        })),

      addTempAssignment: (a) => {
        const assignment: TempAssignment = {
          ...a,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString(),
        }
        set((s) => ({
          settings: {
            ...s.settings,
            roleManagement: {
              ...s.settings.roleManagement,
              tempAssignments: [...s.settings.roleManagement.tempAssignments, assignment],
            },
          },
        }))
      },

      removeTempAssignment: (id) =>
        set((s) => ({
          settings: {
            ...s.settings,
            roleManagement: {
              ...s.settings.roleManagement,
              tempAssignments: s.settings.roleManagement.tempAssignments.filter((a) => a.id !== id),
            },
          },
        })),
    }),
    {
      name: 'epms-company',
      // Merge persisted state with DEFAULTS so new fields are always present
      merge: (persisted, current) => ({
        ...current,
        settings: { ...DEFAULTS, ...(persisted as CompanyStoreState).settings },
      }),
    }
  )
)
