import { useState } from 'react'
import { Chip } from './Chip'
import { Input } from './shell'
import { CHOOSING } from './submitOnEnter'

export interface TagInputProps {
  values: string[]
  onChange: (values: string[]) => void
  /** The text typed but not yet added — the owner adds it on submit too. */
  draft: string
  onDraftChange: (draft: string) => void
  label: string
  type?: 'text' | 'number' | 'date' | 'datetime-local'
  autoFocus?: boolean
}

/** Several values as tags: Enter (or a comma, for text) adds the typed value, and with nothing typed goes to the form;
 *  Backspace in an empty box removes the last one. */
export function TagInput({ values, onChange, draft, onDraftChange, label, type = 'text', autoFocus }: TagInputProps) {
  const [focused, setFocused] = useState(false)
  const add = () => {
    const v = draft.trim()
    if (v && !values.includes(v)) onChange([...values, v])
    onDraftChange('')
  }
  return (
    <div className={`flex flex-wrap items-center gap-1 rounded-lg border bg-white p-1 ${focused ? 'border-neutral-400' : 'border-neutral-200'}`}>
      {values.map((v) => (
        <Chip key={v} onRemove={() => onChange(values.filter((x) => x !== v))} removeLabel={`Remove ${v}`}>{v}</Chip>
      ))}
      <Input
        {...(draft.trim() ? CHOOSING : {})}
        aria-label={label}
        type={type}
        value={draft}
        autoFocus={autoFocus}
        placeholder={values.length ? 'or…' : 'Value — Enter adds another'}
        className="h-7 min-w-[8rem] flex-1 border-0 px-1 shadow-none focus-visible:ring-0"
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(e) => {
          const v = e.target.value
          if (type === 'text' && v.endsWith(',')) {
            onDraftChange(v.slice(0, -1))
            setTimeout(add)
          } else onDraftChange(v)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && draft.trim()) {  // an empty box: Enter belongs to the form
            e.preventDefault()
            add()
          } else if (e.key === 'Backspace' && draft === '' && values.length) onChange(values.slice(0, -1))
        }}
      />
    </div>
  )
}
