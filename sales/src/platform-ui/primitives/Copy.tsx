import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false  // no clipboard (an insecure origin, a denied permission): nothing copied
  }
}

function useCopied(): [boolean, (text: string) => void] {
  const [done, setDone] = useState(false)
  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => setDone(false), 1500)
    return () => clearTimeout(t)
  }, [done])
  return [done, (text) => { void copy(text).then(setDone) }]
}

/** A small icon that copies `text`; a check shows it worked. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, run] = useCopied()
  return (
    <button type="button" aria-label={done ? 'Copied' : label} title={done ? 'Copied' : label} onClick={() => run(text)}
      className="inline-flex h-6 w-6 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
      {done ? <Check className="h-3.5 w-3.5 text-success-600" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  )
}

/** A value as a chip that copies itself on click (a tracking number, a lot). */
export function CopyChip({ text, className }: { text: string; className?: string }) {
  const [done, run] = useCopied()
  return (
    <button type="button" onClick={() => run(text)} aria-label={`Copy ${text}`} title={done ? 'Copied' : 'Copy'}
      className={cn('inline-flex items-center gap-1 rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5 font-mono text-xs text-neutral-700',
        'hover:border-neutral-300 hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600', className)}>
      {text}
      {done ? <Check className="h-3 w-3 text-success-600" /> : <Copy className="h-3 w-3 text-neutral-400" />}
    </button>
  )
}
