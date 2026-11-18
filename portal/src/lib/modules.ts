/**
 * The UniOps module registry: one entry per module, the single place its name,
 * description and icon are written. Portal Home's module cards and the sign-in
 * page's module list both read it, so a new module (Safety, CMMS …) is added
 * here once and shows up in both.
 *
 * Links, health checks and who may open a module stay where they are decided
 * (PortalHome / navConfig) — this file is words and icons only.
 */
import type { LucideIcon } from 'lucide-react'
import { Briefcase, CalendarClock, Landmark, Network, Receipt, UserCheck, Warehouse } from 'lucide-react'

export type ModuleKey = 'epms' | 'oa' | 'finance' | 'vms' | 'booking' | 'mrp' | 'inventory'

export interface ModuleInfo {
  key: ModuleKey
  label: string
  /** One line for the Portal Home card. */
  description: string
  /** Shorter line for the sign-in page. */
  summary: string
  icon: LucideIcon
}

export const MODULES: readonly ModuleInfo[] = [
  { key: 'epms', label: 'EPMS', icon: Briefcase,
    description: 'Enterprise procurement, purchase orders, goods receipt, invoices',
    summary: 'Requisitions, orders, receipts, invoices' },
  { key: 'oa', label: 'OA', icon: Receipt,
    // Vendor payments left OA (PRD OA §15) — expenses, mileage and travel only.
    description: 'Expense reimbursements, mileage and travel',
    summary: 'Expenses, mileage and travel' },
  { key: 'finance', label: 'Finance', icon: Landmark,
    description: 'AP/AR, general ledger, payments, bank rec, budgets, tax',
    summary: 'AP, ledger, payments, bank reconciliation' },
  { key: 'vms', label: 'VMS', icon: UserCheck,
    description: 'Visitor appointments, badge printing, on-site tracking',
    summary: 'Visitors, badges, on-site tracking' },
  { key: 'booking', label: 'Meeting rooms', icon: CalendarClock,
    description: 'Find and book meeting rooms',
    summary: 'Find and book a room' },
  { key: 'mrp', label: 'MRP', icon: Network,
    description: 'Demand forecast, MPS production planning, BOM explorer',
    summary: 'Forecast, production plan, BOM' },
  { key: 'inventory', label: 'Inventory', icon: Warehouse,
    description: 'WMS and NC stock side by side — reconcile, explain differences, trace batches',
    summary: 'WMS and NC stock side by side' },
]

export const MODULE: Record<ModuleKey, ModuleInfo> =
  Object.fromEntries(MODULES.map((m) => [m.key, m])) as Record<ModuleKey, ModuleInfo>
