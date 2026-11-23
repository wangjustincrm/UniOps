import type { ReactNode } from 'react'

/** shadcn's Kbd: a key or shortcut named in text (J / K, Esc, Space). */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-neutral-300 bg-neutral-50 px-1 font-mono text-[11px] font-medium text-neutral-700">
      {children}
    </kbd>
  )
}
