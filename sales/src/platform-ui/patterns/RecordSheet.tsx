import { useContext, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, Maximize2, Minimize2, MoreHorizontal } from 'lucide-react'
import { CopyButton } from '../primitives/Copy'
import { DropdownMenu, type MenuItem } from '../primitives/DropdownMenu'
import { IconButton } from '../primitives/IconButton'
import { Sheet, SheetClose, SheetTitle, type SheetSize } from '../primitives/Sheet'
import { Button, Skeleton } from '../primitives/shell'
import { EmptyState } from '../primitives/States'
import { Tabs } from '../primitives/Tabs'
import { UnsavedBoundary, confirmDiscard, useUnsavedScope } from '../primitives/unsaved'
import { BusyBar } from '../primitives/BusyBar'
import { useDelayed } from '../primitives/useDelayed'
import { cn } from '../lib/cn'
import { ErrorView } from './ErrorMessage'
import { RefreshControl } from './RefreshControl'
import { TableStatusBar } from './TableStatusBar'
import { NoticesContext, createNoticeStore } from './notices'
import type { RefreshState } from './useRefresh'
import { PermissionContext } from './permissions'
import { RecordSheetHostContext, type RecordSheetSize } from './recordSheetHost'

export { RecordSheetHostContext } from './recordSheetHost'
export type { RecordSheetHost, RecordSheetSize } from './recordSheetHost'

// ── the sheet ────────────────────────────────────────────────────────────────

export type FactTone = 'neutral' | 'info' | 'warning' | 'danger' | 'success'

export interface RecordFact {
  label: string
  value: ReactNode
  tone?: FactTone
  /** A second, smaller line (e.g. the currency, "of 480 ordered"). */
  hint?: ReactNode
}

export interface RecordTab {
  id: string
  label: string
  count?: number
  /** A dot on the tab: something in it needs attention. */
  tone?: 'warning' | 'danger'
  hidden?: boolean
  /** Shown instead of `render` (the tab has nothing yet). */
  empty?: string | false
  render: () => ReactNode
}

export interface RecordMenuItem extends MenuItem {
  /** Shown only with this permission. */
  permission?: string
}

export interface RecordSheetProps {
  title: string
  /** Copy the title (e.g. an Order ref) with an icon beside it (default on). */
  copyTitle?: boolean
  subtitle?: ReactNode
  badges?: ReactNode
  facts?: RecordFact[]
  /** The primary actions (ActionButtons with their permission). */
  actions?: ReactNode
  /** Further actions, in the "More" menu. */
  more?: RecordMenuItem[]
  tabs: RecordTab[]
  /** Under the tabs, outside them (e.g. a form's own buttons are inside a tab instead). */
  footer?: ReactNode
  /** The record's refresh (useRefresh over its query, auto: 'follow'). Without it, the
   *  list's (the host's): the record is one of its rows. */
  refresh?: RefreshState
  loading?: boolean
  error?: unknown
  errorMessages?: Record<string, string>
  size?: RecordSheetSize
  /** Without a host (ListPage): the sheet's own open state. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

const EXPANDED_KEY = 'platform-ui.recordSheet.expanded'

function readExpanded(): boolean {
  try { return localStorage.getItem(EXPANDED_KEY) === '1' } catch { return false }
}
function writeExpanded(on: boolean): void {
  try { localStorage.setItem(EXPANDED_KEY, on ? '1' : '0') } catch { /* no storage (private mode): not remembered */ }
}


const FACT_TONE: Record<FactTone, string> = {
  neutral: 'border-neutral-200 bg-white',
  info: 'border-primary-200 bg-primary-50',
  warning: 'border-warning-300 bg-warning-50',
  danger: 'border-danger-200 bg-danger-50',
  success: 'border-success-200 bg-white',
}

/** The record's key numbers, as tiles under the header. */
function Facts({ facts }: { facts: RecordFact[] }) {
  return (
    <dl aria-label="Key facts" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-[repeat(auto-fit,minmax(8rem,1fr))]">
      {facts.map((f) => (
        <div key={f.label} data-tone={f.tone ?? 'neutral'} className={cn('flex min-w-0 flex-col gap-0.5 rounded-lg border px-3 py-2', FACT_TONE[f.tone ?? 'neutral'])}>
          <dt className="truncate text-xs font-medium text-neutral-500">{f.label}</dt>
          <dd className="truncate text-base font-semibold tabular-nums text-neutral-900">{f.value}</dd>
          {f.hint && <dd className="truncate text-xs text-neutral-500">{f.hint}</dd>}
        </div>
      ))}
    </dl>
  )
}

const typing = (el: EventTarget | null) => {
  const t = el as HTMLElement | null
  return !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.getAttribute('role') === 'combobox')
}

/** One record in the standard detail sheet (the user's ask, 2026-11-03): a
 *  sticky header (title with copy, subtitle, badges; refresh with its age,
 *  previous / next record, wide / full width, close), the key facts as tiles,
 *  the actions (primary buttons + "More"), and the record's sections as tabs
 *  (counts, attention dots, rendered only when chosen). Opened by a ListPage
 *  (its host): J / K or Alt+↑ / Alt+↓ go to the previous / next row, the tab
 *  asked for by a link opens first and the chosen tab is kept from record to
 *  record. Loading shows a skeleton, an error its text with Try again. */
export function RecordSheet(props: RecordSheetProps) {
  const host = useContext(RecordSheetHostContext)
  const can = useContext(PermissionContext)
  const visible = props.tabs.filter((t) => !t.hidden)
  // The host (a list) keeps the tab across records and takes a linked one; alone, the sheet keeps its own.
  const [own, setOwn] = useState<string | undefined>(undefined)
  const chosen = host ? host.tab : own
  const tab = visible.some((t) => t.id === chosen) ? chosen! : visible[0]?.id
  const [expanded, setExpanded] = useState(readExpanded)
  // THE refresh (useRefresh): the record's own, else its list's. A click and an
  // auto tick look the same: the bar under the header and the spin at once (≥ 600 ms);
  // a background refetch shows the bar after 200 ms.
  const r = props.refresh ?? host?.refresh
  // The sheet's own notices (its status row, pinned at its bottom): its refresh's, and what the
  // actions inside it report (useNotices). A sheet refreshed by its list has a scope of its own.
  const [ownNotices] = useState(createNoticeStore)
  const notices = props.refresh?.notices ?? ownNotices
  const backgroundBar = useDelayed(!!r?.fetching && !props.loading)
  const showBar = !!r?.busy || backgroundBar
  const open = host ? host.open : !!props.open
  const close = () => (host ? host.close() : props.onOpenChange?.(false))
  const size: SheetSize = expanded ? 'full' : (props.size ?? host?.size ?? 'lg')

  // Unsaved input (2026-11-10): the sheet's scope, and the open tab's inside it. Leaving the tab asks
  // about the tab's forms only; closing the sheet (or prev / next in the list) about all of them.
  const sheetScope = useUnsavedScope()
  const tabScope = useUnsavedScope(sheetScope)
  const choose = (id: string) => (host?.onTabChange ? host.onTabChange(id) : setOwn(id))
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // React bubbles through portals: a key in a dialog or menu opened from this sheet is not for the sheet.
    if (!e.currentTarget.contains(e.target as Node) || typing(e.target) || e.ctrlKey || e.metaKey) return
    const back = e.key === 'k' || e.key === 'K' || (e.altKey && e.key === 'ArrowUp')
    const fwd = e.key === 'j' || e.key === 'J' || (e.altKey && e.key === 'ArrowDown')
    if (back && host?.prev) { e.preventDefault(); host.prev() }
    if (fwd && host?.next) { e.preventDefault(); host.next() }
  }
  const more = (props.more ?? []).filter((m) => !m.permission || can(m.permission))

  const header = (
    <div className="relative flex flex-col gap-1 border-b border-neutral-200 bg-white px-4 py-3 sm:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <SheetTitle className="truncate text-lg">{props.title}</SheetTitle>
          {props.copyTitle !== false && props.title && <CopyButton text={props.title} label={`Copy ${props.title}`} />}
          {props.badges}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {r && <RefreshControl state={{ ...r, setAuto: undefined }} />}
          {host && (host.prev || host.next) && (
            <>
              <IconButton label="Previous record (K)" icon={<ChevronUp className="h-4 w-4" />} disabled={!host.prev} onClick={() => host.prev?.()} />
              <IconButton label="Next record (J)" icon={<ChevronDown className="h-4 w-4" />} disabled={!host.next} onClick={() => host.next?.()} />
              {host.position && <span className="hidden px-1 text-xs tabular-nums text-neutral-400 sm:inline">{host.position}</span>}
            </>
          )}
          <IconButton label={expanded ? 'Narrower' : 'Full width'} className="hidden sm:inline-flex"
            icon={expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            onClick={() => { setExpanded(!expanded); writeExpanded(!expanded) }} />
          <SheetClose />
        </div>
      </div>
      {props.subtitle && <p className="truncate text-sm text-neutral-500">{props.subtitle}</p>}
      <BusyBar active={showBar} className="bottom-0 top-auto -mb-px" />
    </div>
  )

  let body: ReactNode
  if (props.error) {
    body = (
      <div className="flex flex-col items-start gap-2">
        <ErrorView error={props.error} messages={props.errorMessages} />
        {r && <Button variant="secondary" size="sm" onClick={() => { void r.refresh() }}>Try again</Button>}
      </div>
    )
  } else if (props.loading) {
    body = (
      <div aria-busy="true" aria-label="Loading" className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-4">
        {props.facts && props.facts.length > 0 && <Facts facts={props.facts} />}
        {(props.actions || more.length > 0) && (
          <div role="toolbar" aria-label="Actions" className="flex flex-wrap items-center gap-2">
            {props.actions}
            {more.length > 0 && (
              <DropdownMenu label="More actions" align="end" items={more}
                trigger={<Button variant="secondary" size="sm" aria-label="More actions"><MoreHorizontal className="mr-1 h-4 w-4" />More</Button>} />
            )}
          </div>
        )}
        {visible.length > 0 && (
          <Tabs ariaLabel="Sections" value={tab} onValueChange={choose} canLeave={() => confirmDiscard(tabScope)}
            listClassName="sticky top-0 z-10 -mx-4 bg-white px-4 sm:-mx-6 sm:px-6"
            items={visible.map((t) => ({
              value: t.id, label: t.label, count: t.count, attention: t.tone,
              content: t.empty ? <EmptyState title={t.empty} className="p-6" />
                : <UnsavedBoundary scope={tabScope}><TabBody render={t.render} /></UnsavedBoundary>,
            }))} />
        )}
        {props.footer}
      </div>
    )
  }

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) close() }} title={props.title || 'Record'} size={size} unsaved={sheetScope}
      header={header} onKeyDown={onKeyDown} bodyClassName="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-4 sm:px-6"
      statusBar={<TableStatusBar store={notices} className="shrink-0 px-4 sm:px-6" />}>
      <NoticesContext.Provider value={notices}>{body}</NoticesContext.Provider>
    </Sheet>
  )
}

/** A tab's content, rendered only when the tab is shown (its render runs then, hooks and all). */
function TabBody({ render }: { render: () => ReactNode }) {
  return <div className="flex flex-col gap-4">{render()}</div>
}

/** A form in the standard sheet (settings, an edit that is not a record's
 *  detail). A record's detail is a RecordSheet. */
export function FormSheet({ open, onOpenChange, title, description, children, size = 'md' }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string; children: ReactNode
  size?: RecordSheetSize
}) {
  return <Sheet open={open} onOpenChange={onOpenChange} title={title} description={description} size={size}>{children}</Sheet>
}
