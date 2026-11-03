import { useRef, type ReactNode } from 'react'
import { Button } from './shell'
import { TipButton } from './TipButton'

export interface FilePickerProps {
  /** e.g. ".csv,text/csv" */
  accept?: string
  /** The hidden input's accessible name (e.g. "CSV file"). */
  label: string
  onFile: (file: File) => void
  children: ReactNode
  disabled?: boolean
  /** A toolbar icon instead of a labelled button: `children` is then its tooltip and name. */
  icon?: ReactNode
}

/** A button that opens the file chooser; the same file can be chosen again. */
export function FilePicker({ accept, label, onFile, children, disabled, icon }: FilePickerProps) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <>
      <input ref={ref} type="file" accept={accept} className="hidden" aria-label={label} disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''  // choosing the same file again fires again
          if (file) onFile(file)
        }} />
      {icon ? (
        <TipButton label={String(children)} disabled={disabled} onClick={() => ref.current?.click()}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 disabled:opacity-40">
          {icon}
        </TipButton>
      ) : <Button variant="secondary" size="sm" disabled={disabled} onClick={() => ref.current?.click()}>{children}</Button>}
    </>
  )
}
