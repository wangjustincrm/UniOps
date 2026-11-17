import type { ReactNode } from 'react'
import { PageHelp, type HelpId } from '../help'

/** A page's title and what it is for; actions sit on the right. `help`: a ? beside the title opening
 *  the page's help panel (what it is for, its common tasks, a search over all help). */
export function PageHeader({ title, description, actions, help, helpSections }: {
  title: string; description?: ReactNode; actions?: ReactNode; help?: HelpId; helpSections?: { title: string; ids: string[] }[]
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-1">
          <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
          {help && <PageHelp id={help} page={title} sections={helpSections} />}
        </div>
        {description && <p className="text-sm text-neutral-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}
