import { CheckboxList } from './Checkbox'
import { MultiSelect, type MultiOption } from './MultiSelect'

export interface MultiChoiceProps {
  options: MultiOption[]
  values: string[]
  onChange: (values: string[]) => void
  label: string
  /** The tags box with its list by default; checkboxes when every choice should stay in view. */
  display?: 'dropdown' | 'checkboxes'
  placeholder?: string
  disabled?: boolean
}

/** Several values from a known list, in the module's one way unless a page asks for checkboxes. */
export function MultiChoice({ display = 'dropdown', ...props }: MultiChoiceProps) {
  return display === 'checkboxes' ? <CheckboxList {...props} /> : <MultiSelect {...props} />
}
