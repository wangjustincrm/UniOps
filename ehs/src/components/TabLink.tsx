/**
 * A link that also moves an already-open tab to where it points.
 *
 * ★ Review (uniops-8b): a page told what to show by its query — the incident
 * register's figures, a hazard's row, a JHSC recommendation, a chemical's
 * sheet, a toolbox delivery, a safety equipment item — is a static tab, and
 * the shell only focuses one that is already open; it does not overwrite its
 * path. So a second link to it from elsewhere showed what the first had. Use
 * this wherever the target comes from data (tasks, calendar rows, figures,
 * sources); a link with no query is unaffected.
 */
import type { ComponentProps } from 'react'
import { Link } from 'react-router-dom'
import { useRetargetTab } from '@/hooks/useRetargetTab'

export default function TabLink({ to, onClick, ...rest }: ComponentProps<typeof Link> & { to: string }) {
  const retarget = useRetargetTab()
  return (
    <Link to={to} {...rest}
          onClick={(e) => {
            // ★ Review (uniops-8b): a modified or middle click opens a browser
            // tab and this page does not move — the app's own open tab must
            // not be repointed behind the reader's back.
            if (!(e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey
                  || e.shiftKey || e.altKey)) retarget(to)
            onClick?.(e)
          }} />
  )
}
