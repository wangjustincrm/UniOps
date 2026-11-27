import type { ReactNode } from 'react'
import { actionIcon } from '../primitives/actionIcons'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { TourMark } from '../help'
import { errorText } from '../resource/businessError'
import type { NoticeStore } from './notices'
import type { ExportColumns } from './QueryTable'

/** Export CSV: the visible columns as the list shows them (the default), or every field of the list's own export.
 *  A visible column the file cannot carry is named, in the menu and after: never dropped without a word. */
export function ExportMenu({ columns, run, notices, errorMessages, icon }: {
  columns: ExportColumns; run: (columns?: string[]) => Promise<void>; notices: NoticeStore; errorMessages?: Record<string, string>
  /** The list's own icon (ListPage exportIcon); default: the platform's export icon. */
  icon?: ReactNode
}) {
  const left = columns.left.join(', ')
  const go = (ids: string[] | undefined) => {
    Promise.resolve().then(() => run(ids)).then(
      () => notices.notify({ key: 'csv', tone: 'success', text: ids && left ? `CSV exported, without ${left}` : 'CSV exported' }),
      (e: unknown) => notices.notify({ key: 'csv', tone: 'error', text: `Export failed: ${errorText(e, errorMessages)}` }))
  }
  return (
    <TourMark help="list.export" slot="list.export" className="inline-flex">
      <DropdownMenu label="Export CSV" align="end"
        items={[
          { label: `Visible columns (${columns.ids.length})`, onSelect: () => go(columns.ids), disabled: columns.ids.length === 0,
            disabledReason: 'No column shown can be exported' },
          { label: 'All fields', onSelect: () => go(undefined) },
          ...(left ? [{ label: `Not in the file: ${left}`, separated: true, disabled: true, onSelect: () => {} }] : []),
        ]}
        trigger={
          <button type="button" aria-label="Export CSV" className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            {actionIcon('export', icon, 'h-4 w-4')}
          </button>
        } />
    </TourMark>
  )
}
