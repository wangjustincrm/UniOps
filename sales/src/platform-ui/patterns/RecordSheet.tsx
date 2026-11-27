import { useContext, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, Maximize2, Minimize2 } from 'lucide-react'
import { CopyButton } from '../primitives/Copy'
import type { MenuItem } from '../primitives/DropdownMenu'
import { Callout } from '../primitives/Callout'
import { IconButton } from '../primitives/IconButton'
import { Sheet, SheetClose, SheetTitle, type SheetSize } from '../primitives/Sheet'
import { Button, Skeleton } from '../primitives/shell'
import { EmptyState } from '../primitives/States'
import { AutoGrid } from '../primitives/Layout'
import { HelpTip, ShowMeAround, TourMark, TourScope, useScopeId, type HelpId } from '../help'
import { Tabs } from '../primitives/Tabs'
import { UnsavedBoundary, useUnsavedScope, whenDiscarded } from '../primitives/unsaved'
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
import type { BarItem } from './actionBar'
import { RecordActionBar } from './RecordActionBar'
import { ReadOnlyContext, READ_ONLY_HERE, RecordContainerContext, RecordFromContext, useRecordNav, useRecordTabOpen, type RecordKey } from './recordNav'
import { copyLink } from './links'

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
  /** A help id: a ? beside the label. */
  help?: HelpId
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
  /** It changes the record (Deactivate): not offered while the record is read-only here. */
  changes?: boolean
  /** Its onSelect runs as an action (useAction): a failure, and with `done` a success, is said in this
   *  sheet's status row; `failPrefix` before the error (e.g. "Download failed"). */
  done?: string
  failPrefix?: string
}

export interface RecordSheetProps {
  title: string
  /** Copy the title (e.g. an Order ref) with an icon beside it (default on). */
  copyTitle?: boolean
  subtitle?: ReactNode
  badges?: ReactNode
  facts?: RecordFact[]
  /** The record, when its type has a page of its own: "Open in its tab" and "Copy link" (built in), and the sheet
   *  read-only while that tab is open. */
  record?: RecordKey
  /** The record's own actions in its action bar (RecordActionBar: at most two buttons, the rest in "⋯"). */
  bar?: BarItem[]
  /** Not yet in the action bar: buttons under the facts (ActionButtons with their permission). */
  actions?: ReactNode
  /** Further actions (a file, a copy): in the action bar's "⋯". */
  more?: RecordMenuItem[]
  /** Above the tabs: a form an action opened (e.g. a draft being edited). */
  panel?: ReactNode
  /** The shown tab, when the record's own code chooses it (an action that opens its form in a tab); else the
   *  list's (a sheet) or the view's own. */
  tab?: string
  onTabChange?: (id: string) => void
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
    <AutoGrid as="dl" aria-label="Key facts" min="8.5rem" gap={2}>
      {facts.map((f) => (
        <div key={f.label} data-tone={f.tone ?? 'neutral'} className={cn('flex min-w-0 flex-col gap-0.5 rounded-lg border px-3 py-2', FACT_TONE[f.tone ?? 'neutral'])}>
          <dt className="flex min-w-0 items-center gap-0.5 text-xs font-medium text-neutral-500"><span className="truncate">{f.label}</span>{f.help && <HelpTip id={f.help} className="h-4 w-4" />}</dt>
          <dd className="truncate text-base font-semibold tabular-nums text-neutral-900">{f.value}</dd>
          {f.hint && <dd className="truncate text-xs text-neutral-500">{f.hint}</dd>}
        </div>
      ))}
    </AutoGrid>
  )
}

const typing = (el: EventTarget | null) => {
  const t = el as HTMLElement | null
  return !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.getAttribute('role') === 'combobox')
}

/** One record in the standard record view (the user's ask, 2026-11-03; two containers, 2026-11-27): a sticky
 *  header (title with copy, subtitle, badges; the action bar — at most two buttons, the rest in "⋯"; refresh with
 *  its age; in a list's sheet previous / next record, wide / full width, close; in its own tab "← Back to …"), the
 *  key facts as tiles, and the record's sections as tabs (counts, attention dots, rendered only when chosen).
 *  In a list's sheet (its host): J / K or Alt+↑ / Alt+↓ go to the previous / next row, the tab asked for by a
 *  link opens first and the chosen tab is kept from record to record; "Open in its tab" moves it to its own tab,
 *  and while that tab is open the sheet is read-only and says so. In its own tab (RecordContainerContext): the
 *  same view as a page. Loading shows a skeleton, an error its text with Try again. */
export function RecordSheet(props: RecordSheetProps) {
  const host = useContext(RecordSheetHostContext)
  const container = useContext(RecordContainerContext)
  const inTab = container?.kind === 'tab'
  const can = useContext(PermissionContext)
  const nav = useRecordNav()
  const href = props.record && nav ? nav.href(props.record) : null
  const tabOpen = useRecordTabOpen(props.record)
  const readOnly = !inTab && tabOpen
  const visible = props.tabs.filter((t) => !t.hidden)
  // The host (a list) keeps the tab across records and takes a linked one; alone, the view keeps its own.
  const [own, setOwn] = useState<string | undefined>(undefined)
  // Its tour scope is named after its list's page (a record of Orders: record:Orders), so what is seen
  // on one order is seen on the next, and an invoice's own stops are still new.
  const pageScope = useScopeId()
  const chosen = props.tab ?? (host && !inTab ? host.tab : own)
  const tab = visible.some((t) => t.id === chosen) ? chosen! : visible[0]?.id
  const [expanded, setExpanded] = useState(readExpanded)
  // THE refresh (useRefresh): the record's own, else its list's. A click and an
  // auto tick look the same: the bar under the header and the spin at once (≥ 600 ms);
  // a background refetch shows the bar after 200 ms.
  const r = props.refresh ?? (inTab ? undefined : host?.refresh)
  // The view's own notices (its status row, pinned at its bottom): its refresh's, and what the
  // actions inside it report (useNotices). A sheet refreshed by its list has a scope of its own.
  const [ownNotices] = useState(createNoticeStore)
  const notices = props.refresh?.notices ?? ownNotices
  const backgroundBar = useDelayed(!!r?.fetching && !props.loading)
  const showBar = !!r?.busy || backgroundBar
  const open = host && !inTab ? host.open : !!props.open
  const close = () => (inTab ? container.close() : host ? host.close() : props.onOpenChange?.(false))
  const size: SheetSize = expanded ? 'full' : (props.size ?? host?.size ?? 'lg')

  // Unsaved input (2026-11-10): the view's scope, and the open tab's inside it. Leaving the tab asks
  // about the tab's forms only; closing the sheet (or prev / next in the list) about all of them.
  const sheetScope = useUnsavedScope()
  const tabScope = useUnsavedScope(sheetScope)
  const choose = (id: string) => (props.onTabChange ? props.onTabChange(id) : host?.onTabChange && !inTab ? host.onTabChange(id) : setOwn(id))
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // React bubbles through portals: a key in a dialog or menu opened from this sheet is not for the sheet.
    if (inTab || !e.currentTarget.contains(e.target as Node) || typing(e.target) || e.ctrlKey || e.metaKey) return
    const back = e.key === 'k' || e.key === 'K' || (e.altKey && e.key === 'ArrowUp')
    const fwd = e.key === 'j' || e.key === 'J' || (e.altKey && e.key === 'ArrowDown')
    if (back && host?.prev) { e.preventDefault(); host.prev() }
    if (fwd && host?.next) { e.preventDefault(); host.next() }
  }
  const goToTab = () => whenDiscarded(sheetScope, () => { close(); nav!.open(props.record!) })
  // The action bar: the record's own (its `bar`, then `more`), then the built-in ones.
  const items: BarItem[] = [
    ...(props.bar ?? []),
    ...(props.more ?? []).filter((m) => !m.permission || can(m.permission)).map((m, n): BarItem => ({
      id: `more:${n}:${m.label}`, label: m.label, group: 'record', order: 1000 + n, changes: !!m.changes, opensDialog: m.opensDialog, tone: m.tone,
      fit: m.disabled ? (m.disabledReason ?? 'Not now') : true, done: m.done, failPrefix: m.failPrefix, onSelect: m.onSelect,
    })),
    ...(href && !inTab && !readOnly ? [{ id: 'builtin:open-tab', label: 'Open in its tab', primary: 0, group: 'builtin' as const, refreshes: false,
      hint: 'The record in a tab of its own: a link to it opens there too', onSelect: goToTab }] : []),
    ...(href ? [{ id: 'builtin:copy-link', label: 'Copy link', group: 'builtin' as const, refreshes: false, done: 'Link copied', failPrefix: 'Copy failed',
      onSelect: () => copyLink(href) }] : []),
    ...(r ? [{ id: 'builtin:refresh', label: 'Refresh', group: 'builtin' as const, refreshes: false, onSelect: () => r.refresh() }] : []),
  ]

  const Title = inTab ? 'h1' : SheetTitle
  const header = (
    <div className={cn('relative flex flex-col gap-1 border-b border-neutral-200 bg-white px-4 py-3 sm:px-6', inTab && 'sticky top-0 z-20')}>
      {inTab && container.back && <div className="text-sm">{container.back}</div>}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <TourMark as="div" help="record.header" slot="record.header" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Title className="truncate text-lg font-semibold text-neutral-900">{props.title}</Title>
          {props.copyTitle !== false && props.title && <CopyButton text={props.title} label={`Copy ${props.title}`} />}
          {props.badges}
        </TourMark>
        <div className="flex shrink-0 items-center gap-0.5">
          {!props.loading && !props.error && (
            <TourMark as="div" help="record.actions" slot="record.actions" className="mr-1 inline-flex"><RecordActionBar items={items} /></TourMark>
          )}
          <ShowMeAround />
          {r && <RefreshControl state={{ ...r, setAuto: undefined }} />}
          {!inTab && host && (host.prev || host.next) && (
            <>
              <IconButton label="Previous record (K)" icon={<ChevronUp className="h-4 w-4" />} disabled={!host.prev} onClick={() => host.prev?.()} />
              <IconButton label="Next record (J)" icon={<ChevronDown className="h-4 w-4" />} disabled={!host.next} onClick={() => host.next?.()} />
              {host.position && <span className="hidden px-1 text-xs tabular-nums text-neutral-500 sm:inline">{host.position}</span>}
            </>
          )}
          {!inTab && (
            <>
              <IconButton label={expanded ? 'Narrower' : 'Full width'} className="hidden sm:inline-flex"
                icon={expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                onClick={() => { setExpanded(!expanded); writeExpanded(!expanded) }} />
              <SheetClose />
            </>
          )}
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
        {readOnly && (
          <Callout label="Open in its tab" tone="info">
            <span className="flex flex-wrap items-center gap-2">
              <span>{props.title} is open in its own tab: change it there.</span>
              <Button size="sm" variant="secondary" onClick={goToTab}>Go to its tab</Button>
            </span>
          </Callout>
        )}
        {props.facts && props.facts.length > 0 && <TourMark as="div" help="record.facts" slot="record.facts"><Facts facts={props.facts} /></TourMark>}
        {props.actions && (
          <div role="toolbar" aria-label="Actions" className="flex flex-wrap items-center gap-2">{props.actions}</div>
        )}
        {props.panel}
        {visible.length > 0 && (
          <Tabs ariaLabel="Sections" listTour={{ help: 'record.tabs', slot: 'record.tabs' }} value={tab} onValueChange={choose} beforeLeave={(leave) => whenDiscarded(tabScope, leave)}
            listClassName={cn('z-10 -mx-4 bg-white px-4 sm:-mx-6 sm:px-6', !inTab && 'sticky top-0')}
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
  // In a list's sheet, a link inside says where it came from: this record ("← Back to Order SMP-1030").
  const from = !inTab && props.record ? { record: props.record, title: props.title } : null
  const content = <RecordFromContext.Provider value={from}><NoticesContext.Provider value={notices}>{body}</NoticesContext.Provider></RecordFromContext.Provider>
  const readOnlyText = readOnly ? READ_ONLY_HERE : null
  const statusBar = <TableStatusBar store={notices} className="shrink-0 px-4 sm:px-6" />

  // Its own tour scope: the record's parts register here, not in the page's tour ("Show me around").
  if (inTab) {
    return (
      <TourScope id={`record:${pageScope ?? 'record'}`} kind="record"><ReadOnlyContext.Provider value={readOnlyText}>
        <section aria-label={props.title || 'Record'} className="flex flex-col rounded-lg border border-neutral-200 bg-white">
          <NoticesContext.Provider value={notices}>{header}</NoticesContext.Provider>
          <UnsavedBoundary scope={sheetScope}>
            <div className="px-4 pb-6 pt-4 sm:px-6">{content}</div>
          </UnsavedBoundary>
          <div className="sticky bottom-0 z-20 border-t border-neutral-100 bg-white">{statusBar}</div>
        </section>
      </ReadOnlyContext.Provider></TourScope>
    )
  }
  return (
    <TourScope id={`record:${pageScope ?? 'record'}`} kind="record"><ReadOnlyContext.Provider value={readOnlyText}>
    <Sheet open={open} onOpenChange={(o) => { if (!o) close() }} title={props.title || 'Record'} size={size} unsaved={sheetScope}
      header={<NoticesContext.Provider value={notices}>{header}</NoticesContext.Provider>} onKeyDown={onKeyDown} bodyClassName="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-4 sm:px-6"
      statusBar={statusBar}>
      {content}
    </Sheet>
    </ReadOnlyContext.Provider></TourScope>
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
