/**
 * A piece of equipment from the equipment register (Settings → Equipment).
 *
 * Round 10 (open box): "there is an equipment register in the settings that
 * does not appear in any of the incident forms. add this equipment as a picker
 * on all forms so that we can identify what piece of equipment contributed to
 * the incidents. Include it on the hazard register, job analysis, and work
 * permits." Offers what is in service; a record that already names one since
 * retired shows it as itself, marked so, and keeps it (the server checks the
 * same).
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { inputClass } from '@/components/ui'

export interface EquipmentItem {
  id: string; asset_no: string | null; name: string
  location_path: string | null; is_active: boolean
}

export function useEquipmentRegister() {
  return useQuery({
    queryKey: ['equipment', 'all'],
    queryFn: () => api.get<EquipmentItem[]>('/api/v1/equipment?include_inactive=true&limit=500'),
    staleTime: 10 * 60_000,
  })
}

export function equipmentLabel(e: EquipmentItem): string {
  return `${e.asset_no ? `${e.asset_no} — ` : ''}${e.name}`
}

/** "EQ-12 — Capper (retired)", for a record's read-only view. */
export function useEquipmentName(id: string | null | undefined): string | null {
  const register = useEquipmentRegister()
  if (!id) return null
  const e = register.data?.find((x) => x.id === id)
  if (!e) return register.isPending ? '…' : 'Equipment not in the register'
  return equipmentLabel(e) + (e.is_active ? '' : ' (retired)')
}

export default function EquipmentSelect({ value, onChange, id, ariaLabel = 'Equipment' }: {
  value: string | null
  onChange: (id: string | null) => void
  id?: string
  ariaLabel?: string
}) {
  const register = useEquipmentRegister()
  const live = (register.data ?? []).filter((e) => e.is_active)
  const current = register.data?.find((e) => e.id === value)
  if (register.isError) {
    return <p className="text-xs text-warning-700">The equipment register could not be loaded.</p>
  }
  return (
    <select id={id} aria-label={ariaLabel} className={inputClass} value={value ?? ''}
            disabled={register.isPending}
            onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{register.isPending ? 'Loading…' : 'None, or not in the register'}</option>
      {current && !current.is_active && (
        <option value={current.id}>{equipmentLabel(current)} (retired)</option>
      )}
      {live.map((e) => (
        <option key={e.id} value={e.id}>
          {equipmentLabel(e)}{e.location_path ? ` · ${e.location_path.split('/').pop()}` : ''}
        </option>
      ))}
    </select>
  )
}

/** " · EQ-12 — Capper", after a record's area in a list or a heading. */
export function EquipmentName({ id }: { id: string | null | undefined }) {
  const name = useEquipmentName(id)
  return name ? <>{` · ${name}`}</> : null
}
