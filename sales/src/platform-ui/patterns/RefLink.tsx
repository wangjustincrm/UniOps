import { useContext, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { cn } from '../lib/cn'
import { BREAKPOINTS, useMediaQuery } from '../primitives/useResponsive'
import { RecordSheetHostContext } from './recordSheetHost'
import { useRecordNav, type RecordKey } from './recordNav'
import { useListMeta, type ListMeta } from '../resource/listMeta'
import { useAction } from './useAction'
import { copyLink, openInBrowserTab } from './links'

const MODIFIED = (e: MouseEvent) => e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey

/** A reference to a record (user 2026-11-27): a real link. A click opens the record's own app tab, or focuses it;
 *  Ctrl/⌘+click, Shift+click and a middle click do what the browser does (a new browser tab or window). Right
 *  click — or Shift+F10 / the Menu key on it — offers Open, Open in a new browser tab, Copy link. Not readable by
 *  this person, or no page for its type here: its text only. Whether this person may read it: `readable` (a list
 *  cell has it from its /meta), or `from` — the resource whose record shows it (its /meta `refs`, the server's). */
export function RefLink({ record, readable, from, children, className }: {
  record: RecordKey; readable?: boolean; from?: { basePath: string; meta: () => Promise<ListMeta> }; children: ReactNode; className?: string
}) {
  const meta = useListMeta(from).data
  const mayRead = readable ?? (from ? !!meta?.refs?.[record.type]?.readable : true)
  const nav = useRecordNav()
  const href = nav?.href(record) ?? null
  // On a phone a list's sheet takes the whole screen (it is not drawn in its tab): going to another record closes it
  // first, or the record's tab would open behind it.
  const host = useContext(RecordSheetHostContext)
  const phone = useMediaQuery(`(max-width: ${BREAKPOINTS.sm - 0.02}px)`)
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null)
  const self = useRef<HTMLAnchorElement>(null)
  const action = useAction({ refreshes: false })
  if (!nav || !href || !mayRead || record.key === '' || record.key == null) return <>{children}</>
  const open = () => { if (phone) host?.close(); nav.open(record) }
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    e.stopPropagation()  // never also the row's own click (its sheet)
    if (MODIFIED(e)) return
    e.preventDefault()
    open()
  }
  const at = (x: number, y: number) => setMenuAt({ x, y })
  const onKeyDown = (e: KeyboardEvent<HTMLAnchorElement>) => {
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault()
      const r = e.currentTarget.getBoundingClientRect()
      at(r.left, r.bottom)
    }
  }
  return (
    <>
      <a ref={self} href={href} onClick={click} onAuxClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); at(e.clientX, e.clientY) }}
        className={cn('text-primary-700 underline decoration-primary-300 underline-offset-2 hover:decoration-primary-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600', className)}>
        {children}
      </a>
      {menuAt && (
        <span data-no-row-click onClick={(e) => e.stopPropagation()}>
          <ContextMenuAt x={menuAt.x} y={menuAt.y} onClose={() => { setMenuAt(null); self.current?.focus() }}
            items={[
              { label: 'Open', onSelect: open },
              { label: 'Open in a new browser tab', hint: 'Ctrl+click or a middle click', onSelect: () => openInBrowserTab(href) },
              { label: 'Copy link', onSelect: () => action.run(() => copyLink(href), { done: 'Link copied', failPrefix: 'Copy failed' }) },
            ]} />
        </span>
      )}
    </>
  )
}

/** A menu opened where the pointer is (a right click): the app's menu over a 0-size anchor at that point. */
export function ContextMenuAt({ x, y, items, onClose, label = 'Link menu' }: {
  x: number; y: number; onClose: () => void; label?: string
  items: { label: string; hint?: string; onSelect: () => unknown; separated?: boolean; disabled?: boolean; disabledReason?: string }[]
}) {
  return (
    <DropdownMenu label={label} open onOpenChange={(o) => { if (!o) onClose() }}
      items={items.map((i) => ({ ...i, onSelect: () => { onClose(); void i.onSelect() } }))}
      trigger={<span aria-hidden style={{ position: 'fixed', left: x, top: y, width: 0, height: 0 }} />} />
  )
}
