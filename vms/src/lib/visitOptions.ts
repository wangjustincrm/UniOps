import type {
  AccessArea, AreaRule, BadgeAreaStyle, BadgeConfig, Visit, VisitPurpose,
} from '@/services/api'

/** Zones a visit can be booked for: the active ones, in admin order. A visit
 *  already in a retired zone keeps it as an option (`keep`). */
export function zoneOptions(
  rules: Record<string, AreaRule> | undefined, keep?: AccessArea,
): { value: AccessArea; label: string }[] {
  return Object.values(rules ?? {})
    .filter((r) => r.is_active || r.area === keep)
    .sort((x, y) => x.sort_order - y.sort_order || x.name.localeCompare(y.name))
    .map((r) => ({ value: r.area, label: r.is_active ? r.name : `${r.name} (retired)` }))
}

/** The zone's display name; falls back to the code ("cold_room" → "cold room"). */
export function zoneName(rules: Record<string, AreaRule> | undefined, area: AccessArea): string {
  return rules?.[area]?.name ?? area.replace(/_/g, ' ')
}

export const PURPOSES: { value: VisitPurpose; label: string }[] = [
  { value: 'meeting',     label: 'Business Meeting' },
  { value: 'maintenance', label: 'Equipment Maintenance' },
  { value: 'tour',        label: 'Factory Tour' },
  { value: 'audit',       label: 'Audit / Inspection' },
  { value: 'interview',   label: 'Interview' },
  { value: 'delivery',    label: 'Delivery' },
  { value: 'other',       label: 'Other' },
]

/** May a confirmed visit move to this zone without new approval? Same rule
 *  as vms-api: the zone needs no approval, or every active manager of it was
 *  already among those the visit was routed to. (The server only counts
 *  active managers; this list may include inactive ones — the server decides.) */
export function mayMoveConfirmed(visit: Pick<Visit, 'zone_approver_ids'>, rule: AreaRule | undefined): boolean {
  if (!rule || !rule.requires_approval) return true
  const routed = new Set(visit.zone_approver_ids ?? [])
  return rule.managers.length > 0 && rule.managers.every((m) => routed.has(m.id))
}

/** One-line description of what a zone needs, from the server's rules. */
export function areaRequirementText(rule: AreaRule | undefined): string | null {
  if (!rule) return null
  const parts: string[] = []
  if (rule.requires_approval) {
    const who = rule.managers.map((m) => m.name).filter(Boolean).join(', ')
    parts.push(`approval${who ? ` by its zone manager (${who})` : ''}`)
  }
  if (rule.requires_health_declaration) parts.push('a passing health declaration for every visitor')
  if (parts.length === 0) return null
  return `This zone needs ${parts.join(' and ')} before the badge can print.`
}

/** Badge band colours / label for a zone. Zones added in VMS Admin have no
 *  entry in the badge config until someone styles them: show the zone's name,
 *  red for GMP-grade and slate otherwise — never another zone's band. */
export function badgeAreaStyle(
  cfg: BadgeConfig, area: AccessArea, rule: AreaRule | undefined,
): BadgeAreaStyle {
  const own = cfg.band.areas[area]
  if (own) return own
  const gmp = rule?.gmp_grade ?? false
  return {
    label: (rule?.name ?? area.replace(/_/g, ' ')).toUpperCase(),
    bg: gmp ? '#DC2626' : '#475569',
    fg: '#FFFFFF',
    risk: gmp ? 'HIGH' : '',
  }
}
