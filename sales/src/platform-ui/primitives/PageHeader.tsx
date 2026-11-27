import type { ReactNode } from 'react'
import { PageHelp, type HelpId } from '../help'

/** A page's title; actions sit on the right. `help`: a ? beside the title opening the page's help panel (what it is
 *  for, its common tasks, a search over all help). `description` is no longer shown under the title (the user,
 *  2026-11-27: pages without the grey sentence); it stays on the pages' code for now, read by nothing else. */
export function PageHeader({ title, actions, help, helpSections }: {
  title: string; description?: ReactNode; actions?: ReactNode; help?: HelpId; helpSections?: { title: string; ids: string[] }[]
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-1">
          <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
          {help && <PageHelp id={help} page={title} sections={helpSections} />}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}
