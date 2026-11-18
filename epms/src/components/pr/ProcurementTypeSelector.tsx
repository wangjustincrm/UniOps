import { Building2, Hammer, Package, Ruler, ShoppingCart, Wrench, type LucideIcon } from 'lucide-react'
import { ChoiceCard } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { ProcurementType } from '@/types'

// Exported for procurementTypes.test.ts. Which types are selectable is a fact
// about this file alone — the API accepts 1..6 — so the assistant's description
// of type 1 as "not yours to raise" is pinned against it here.
export const TYPES: Array<{
  type: ProcurementType
  icon: LucideIcon
  label: string
  sublabel: string
  noBudget?: boolean
  disabled?: boolean
}> = [
  { type: 1, icon: Package, label: 'Type 1', sublabel: 'Raw Mat. / Packaging', noBudget: true, disabled: true },
  { type: 2, icon: ShoppingCart, label: 'Type 2', sublabel: 'Misc / Consumables' },
  { type: 3, icon: Wrench, label: 'Type 3', sublabel: 'Spare Parts' },
  { type: 4, icon: Hammer, label: 'Type 4', sublabel: 'Service' },
  { type: 5, icon: Building2, label: 'Type 5', sublabel: 'Fixed Asset' },
  { type: 6, icon: Ruler, label: 'Type 6', sublabel: 'Project-Related' },
]

interface ProcurementTypeSelectorProps {
  value: ProcurementType | null
  onChange: (type: ProcurementType) => void
}

export function ProcurementTypeSelector({ value, onChange }: ProcurementTypeSelectorProps) {
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
      {TYPES.map(({ type, icon: Icon, label, sublabel, noBudget, disabled }) => {
        const selected = value === type
        return (
          <ChoiceCard
            key={type}
            selected={selected}
            disabled={disabled}
            onClick={() => onChange(type)}
            className="items-center gap-1.5 p-3 text-center"
          >
            <Icon aria-hidden className={cn('h-5 w-5 stroke-[1.5]', selected ? 'text-accent-fg' : 'text-fg-muted')} />
            <span className="text-xs font-semibold text-fg">{label}</span>
            <span className="text-2xs leading-tight text-fg-muted">{sublabel}</span>
            {noBudget && !disabled && (
              <span className="mt-0.5 rounded-md bg-success-soft px-1.5 py-0.5 text-2xs font-medium text-success-fg">
                No Budget Check
              </span>
            )}
          </ChoiceCard>
        )
      })}
    </div>
  )
}
