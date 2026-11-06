import { CheckboxList } from './Checkbox'
import { MultiSelect, type MultiOption } from './MultiSelect'

export const MANY_CHOICES = 6

export interface MultiChoiceProps {
  options: MultiOption[]
  values: string[]
  onChange: (values: string[]) => void
  label: string
  /** Checkboxes show every choice at once, a dropdown saves room. Auto: checkboxes up to MANY_CHOICES. */
  display?: 'auto' | 'checkboxes' | 'dropdown'
  placeholder?: string
  disabled?: boolean
}

/** Several values from a known list, in whichever form suits how many there are. */
export function MultiChoice({ display = 'auto', ...props }: MultiChoiceProps) {
  const dropdown = display === 'dropdown' || (display === 'auto' && props.options.length > MANY_CHOICES)
  return dropdown ? <MultiSelect {...props} /> : <CheckboxList {...props} />
}
