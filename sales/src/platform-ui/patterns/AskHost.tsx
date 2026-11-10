import { useEffect, useRef, useState } from 'react'
import { Dialog } from '../primitives/Dialog'
import { Button } from '../primitives/shell'
import { registerAskHost, type AskSpec } from '../primitives/ask'

/** Shows the questions `ask()` puts, one at a time, in the app's dialog. Mount once at the app root. */
export function AskHost() {
  const [current, setCurrent] = useState<AskSpec | null>(null)
  const queue = useRef<{ spec: AskSpec; resolve: (yes: boolean) => void }[]>([])
  useEffect(() => registerAskHost((spec) => new Promise<boolean>((resolve) => {
    queue.current.push({ spec, resolve })
    if (queue.current.length === 1) setCurrent(spec)
  })), [])
  const answer = (yes: boolean) => {
    queue.current.shift()?.resolve(yes)
    setCurrent(queue.current[0]?.spec ?? null)
  }
  if (!current) return null
  return (
    <Dialog open onOpenChange={(o) => { if (!o) answer(false) }} title={current.title} description={current.description}
      footer={<>
        <Button type="button" variant="secondary" onClick={() => answer(false)}>{current.cancelLabel ?? 'Cancel'}</Button>
        <Button type="button" variant={current.destructive ? 'destructive' : 'primary'} autoFocus onClick={() => answer(true)}>
          {current.confirmLabel ?? 'OK'}
        </Button>
      </>} />
  )
}
