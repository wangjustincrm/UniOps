import type { AccessArea, AreaRule, VisitPurpose } from '@/services/api'

export const ACCESS_AREAS: { value: AccessArea; label: string }[] = [
  { value: 'office',             label: 'Office / Lobby' },
  { value: 'warehouse',          label: 'Warehouse' },
  { value: 'production_non_gmp', label: 'Production (Non-GMP)' },
  { value: 'production_gmp',     label: 'Production (GMP Clean Zone)' },
  { value: 'laboratory',         label: 'Laboratory' },
  { value: 'all',                label: 'Entire Plant' },
]

export const PURPOSES: { value: VisitPurpose; label: string }[] = [
  { value: 'meeting',     label: 'Business Meeting' },
  { value: 'maintenance', label: 'Equipment Maintenance' },
  { value: 'tour',        label: 'Factory Tour' },
  { value: 'audit',       label: 'Audit / Inspection' },
  { value: 'interview',   label: 'Interview' },
  { value: 'delivery',    label: 'Delivery' },
  { value: 'other',       label: 'Other' },
]

/** 0 = no approval, 1 = Department Manager, 2 = + Quality Manager.
 *  Same tiers vms-api uses to decide whether an area change is allowed. */
export function approvalTier(rule: AreaRule | undefined): number {
  if (!rule) return 0
  if (rule.requires_quality_manager) return 2
  return rule.requires_approval ? 1 : 0
}

/** One-line description of what an area needs, from the server's rules. */
export function areaRequirementText(rule: AreaRule | undefined): string | null {
  if (!rule || !rule.requires_approval) return null
  const parts = [rule.requires_quality_manager
    ? 'Department Manager and Quality Manager approval'
    : 'Department Manager approval']
  if (rule.requires_health_declaration) parts.push('a passing health declaration for every visitor')
  return `This area needs ${parts.join(' and ')} before the badge can print.`
}
