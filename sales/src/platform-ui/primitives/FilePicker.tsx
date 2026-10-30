import { useRef, type ReactNode } from 'react'
import { Button } from './shell'

export interface FilePickerProps {
  /** e.g. ".csv,text/csv" */
  accept?: string
  /** The hidden input's accessible name (e.g. "CSV file"). */
  label: string
  onFile: (file: File) => void
  children: ReactNode
  disabled?: boolean
}

/** A button that opens the file chooser; the same file can be chosen again. */
export function FilePicker({ accept, label, onFile, children, disabled }: FilePickerProps) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <>
      <input ref={ref} type="file" accept={accept} className="hidden" aria-label={label} disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''  // choosing the same file again fires again
          if (file) onFile(file)
        }} />
      <Button variant="secondary" size="sm" disabled={disabled} onClick={() => ref.current?.click()}>{children}</Button>
    </>
  )
}
