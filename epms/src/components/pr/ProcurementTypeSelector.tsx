import { cn } from '@/lib/utils'
import type { ProcurementType } from '@/types'

const TYPES: Array<{
  type: ProcurementType
  icon: string
  label: string
  sublabel: string
  noBudget?: boolean
  disabled?: boolean
}> = [
  { type: 1, icon: '📦', label: 'Type 1', sublabel: 'Raw Mat. / Packaging', noBudget: true, disabled: true },
  { type: 2, icon: '🛒', label: 'Type 2', sublabel: 'Misc / Consumables' },
  { type: 3, icon: '🔧', label: 'Type 3', sublabel: 'Spare Parts' },
  { type: 4, icon: '🔨', label: 'Type 4', sublabel: 'Service' },
  { type: 5, icon: '🏗', label: 'Type 5', sublabel: 'Fixed Asset' },
  { type: 6, icon: '📐', label: 'Type 6', sublabel: 'Project-Related' },
]

interface ProcurementTypeSelectorProps {
  value: ProcurementType | null
  onChange: (type: ProcurementType) => void
}

export function ProcurementTypeSelector({ value, onChange }: ProcurementTypeSelectorProps) {
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
      {TYPES.map(({ type, icon, label, sublabel, noBudget, disabled }) => {
        const selected = value === type
        return (
          <button
            key={type}
            type="button"
            disabled={disabled}
            onClick={() => onChange(type)}
            className={cn(
              'relative flex flex-col items-center gap-1.5 rounded-lg border p-3 text-center transition-all',
              disabled
                ? 'cursor-not-allowed border-neutral-200 bg-neutral-50 opacity-40'
                : selected
                  ? 'border-primary-600 bg-primary-600 text-white'
                  : 'border-neutral-200 bg-white text-neutral-700 hover:border-primary-300 hover:bg-primary-50'
            )}
          >
            <span className="text-2xl">{icon}</span>
            <span className={cn('text-xs font-semibold', selected ? 'text-white' : 'text-neutral-500')}>
              {label}
            </span>
            <span className={cn('text-[10px] leading-tight', selected ? 'text-primary-100' : 'text-neutral-400')}>
              {sublabel}
            </span>
            {noBudget && !disabled && (
              <span
                className={cn(
                  'mt-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-medium',
                  selected ? 'bg-success-600 text-white' : 'bg-success-50 text-success-700'
                )}
              >
                No Budget Check
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
