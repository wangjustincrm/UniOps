import * as RS from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '../lib/cn'

export interface SelectOption {
  value: string
  label: string
}

export interface SelectProps {
  value: string | null | undefined
  onChange: (value: string | null) => void
  options: SelectOption[]
  placeholder?: string
  /** Adds a first option that clears the value (e.g. "—" or "All"). */
  emptyLabel?: string
  id?: string
  name?: string
  disabled?: boolean
  'aria-label'?: string
  'aria-invalid'?: boolean
  onBlur?: () => void
  className?: string
}

// Radix forbids "" as an item value; the clearing option uses this one.
const NONE = '__none__'

/** One value from a list (Radix Select): keyboard (↑ ↓ Enter, type to jump),
 *  focus and screen readers handled. `null` = nothing chosen. */
export function Select({
  value, onChange, options, placeholder = 'Choose…', emptyLabel, id, name, disabled, onBlur, className, ...aria
}: SelectProps) {
  return (
    <RS.Root
      value={value ?? (emptyLabel ? NONE : '')}
      onValueChange={(v) => onChange(v === NONE ? null : v)}
      onOpenChange={(open) => { if (!open) onBlur?.() }}
      disabled={disabled}
      name={name}
    >
      <RS.Trigger
        id={id}
        {...aria}
        className={cn(
          'flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-left text-sm text-neutral-900',
          'focus:outline-none focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)]',
          'disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400 data-[placeholder]:text-neutral-400',
          aria['aria-invalid'] && 'border-danger-600 bg-danger-50',
          className,
        )}
      >
        <RS.Value placeholder={placeholder} />
        <RS.Icon><ChevronDown className="h-4 w-4 text-neutral-400" /></RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content position="popper" sideOffset={4}
          className="z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-lg border border-neutral-200 bg-white p-1 text-sm shadow-lg">
          <RS.Viewport>
            {[...(emptyLabel ? [{ value: NONE, label: emptyLabel }] : []), ...options].map((o) => (
              <RS.Item key={o.value} value={o.value}
                className="relative flex cursor-pointer select-none items-center rounded py-1.5 pl-7 pr-2 outline-none data-[highlighted]:bg-neutral-100 data-[disabled]:opacity-40">
                <RS.ItemIndicator className="absolute left-2"><Check className="h-3.5 w-3.5" /></RS.ItemIndicator>
                <RS.ItemText>{o.label}</RS.ItemText>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  )
}
