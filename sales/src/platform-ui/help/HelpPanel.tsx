import * as RD from '@radix-ui/react-dialog'
import { CircleHelp, Search, X } from 'lucide-react'
import { useContext, useMemo, useState } from 'react'
import { HelpContext } from './context'
import { searchHelp } from './search'
import { useHelp } from './useHelp'
import type { HelpEntry, HelpId } from './types'

/** A page's help: a ? beside its title opening a side panel (full width on a phone) with what the page
 *  is for, its common tasks, and a search over every help text the module has. No text for the page:
 *  nothing is shown. `data-help` marks the button (for a tour). */
export function PageHelp({ id, page }: { id: HelpId; page: string }) {
  const entry = useHelp(id)
  const ctx = useContext(HelpContext)
  const [q, setQ] = useState('')
  const all = useMemo(() => (ctx?.catalog ? ctx.catalog() : []), [ctx])
  const hits = useMemo(() => searchHelp(all, q), [all, q])
  if (!entry) return null
  return (
    <RD.Root onOpenChange={(o) => { if (!o) setQ('') }}>
      <RD.Trigger asChild>
        <button type="button" data-help={id} aria-label={`Help: ${page}`}
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <CircleHelp className="h-4 w-4" aria-hidden />
        </button>
      </RD.Trigger>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-black/20" />
        <RD.Content data-help-panel aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-white shadow-xl focus:outline-none sm:w-[26rem] sm:max-w-[90vw]">
          <div className="flex items-start justify-between gap-3 border-b border-neutral-100 px-4 py-3">
            <RD.Title className="text-base font-semibold text-neutral-900">Help: {page}</RD.Title>
            <RD.Close aria-label="Close" className="rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800">
              <X className="h-4 w-4" aria-hidden />
            </RD.Close>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 text-sm text-neutral-700">
            <label className="relative block">
              <span className="sr-only">Search help</span>
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-neutral-400" aria-hidden />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search help (a word, a status, a document…)"
                className="h-9 w-full rounded-md border border-neutral-200 bg-white pl-8 pr-2 text-sm placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-primary-500" />
            </label>
            {q.trim() ? (
              <section aria-label="Search results" className="flex flex-col gap-2">
                {hits.length === 0 ? <p className="text-neutral-500">Nothing found for “{q.trim()}”.</p> : hits.map((h) => <Hit key={h.id} entry={h.entry} />)}
              </section>
            ) : (
              <section aria-label="This page" className="flex flex-col gap-3">
                <p className="leading-relaxed">{entry.body}</p>
                {entry.tasks && entry.tasks.length > 0 && (
                  <div>
                    <p className="mb-1 font-medium text-neutral-900">Common tasks</p>
                    <ul className="list-disc space-y-1 pl-5">{entry.tasks.map((t) => <li key={t}>{t}</li>)}</ul>
                  </div>
                )}
              </section>
            )}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}

function Hit({ entry }: { entry: HelpEntry }) {
  return (
    <article className="rounded-md border border-neutral-200 p-2.5">
      {entry.title && <p className="font-medium text-neutral-900">{entry.title}</p>}
      <p className="leading-snug">{entry.body}</p>
      {entry.next && <p className="mt-1 leading-snug"><span className="font-medium text-neutral-900">Next: </span>{entry.next}</p>}
    </article>
  )
}
