import { useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { ChevronDown, X } from 'lucide-react'
import { useTabStore, type RouteDef, type TabMeta } from '@uniops/shell'
import { ContextMenuAt, Popover } from '@/platform-ui'
import { cn } from '@/lib/utils'
import { recordTabTest, useRecordTabCap, useTabActions } from './tabActions'
import { RECORD_TAB_CAPS, tabSession, useTabSession } from './tabSession'
import { tabIcon } from './tabIcon'

/** Sales' tab bar (stage 4, user 2026-11-27), in place of the shell's, over the shell's store (nothing in the shell
 *  changes): a tab's menu (right click, Shift+F10 or the Menu key) — Refresh, Close (Delete), Close others, Close to the
 *  right, Copy link, Reopen closed tab; ✕ or a middle click closes, an unsaved tab asks first in the app's own dialog;
 *  All tabs ▾ lists every tab with a search, by kind, and the tabs closed lately; the record-tab cap's notice, with
 *  Reopen, under the bar. Esc never closes a tab: it closes the menu or the list it is in. */
export function SalesTabBar({ routes }: { routes: RouteDef[] }) {
  const tabs = useTabStore((s) => s.tabs)
  const activeKey = useTabStore((s) => s.activeKey)
  const notice = useTabSession((s) => s.notice)
  const recent = useTabSession((s) => s.recent)
  const sheets = useTabSession((s) => s.sheets)
  const actions = useTabActions()
  useRecordTabCap(routes)
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null)
  const refs = useRef(new Map<string, HTMLElement>())
  const focusTab = (key: string) => refs.current.get(key)?.focus()

  const openMenu = (key: string, x: number, y: number) => setMenu({ key, x, y })
  const onKey = (e: KeyboardEvent<HTMLElement>, t: TabMeta, i: number) => {
    if (e.key === 'Delete' && t.closable) { e.preventDefault(); void actions.close(t.key) }
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault()
      const r = e.currentTarget.getBoundingClientRect()
      openMenu(t.key, r.left, r.bottom)
    }
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (step) { e.preventDefault(); const n = tabs[(i + step + tabs.length) % tabs.length]; if (n) focusTab(n.key) }
  }
  const menuTab = menu ? tabs.find((t) => t.key === menu.key) : undefined
  const at = menuTab ? tabs.indexOf(menuTab) : -1

  return (
    <div className="shrink-0 border-b border-neutral-200 bg-white">
      <div className="flex h-10 items-stretch gap-1 px-2">
        <div role="tablist" aria-label="Open tabs" className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
          {tabs.map((t, i) => {
            const Icon = tabIcon(t.icon)
            const active = t.key === activeKey
            const sheetOpen = !active && !!sheets[t.key]  // a record left open in its sheet, behind this tab
            return (
              // The tab and its ✕ side by side (a button inside a tab would be one control in another).
              <div key={t.key} role="presentation" className={cn('group flex shrink-0 items-center self-center rounded-md transition-colors',
                active ? 'bg-primary-50' : 'hover:bg-neutral-50')}>
                <div ref={(el) => { if (el) refs.current.set(t.key, el); else refs.current.delete(t.key) }}
                  role="tab" aria-selected={active} tabIndex={active ? 0 : -1} title={sheetOpen ? `${t.title}: a record is open` : t.title}
                  aria-keyshortcuts={t.closable ? 'Delete Shift+F10' : 'Shift+F10'}
                  aria-label={sheetOpen ? `${t.title}, a record is open` : undefined}
                  onClick={() => actions.select(t.key)} onKeyDown={(e) => onKey(e, t, i)}
                  onAuxClick={(e: MouseEvent) => { if (e.button === 1 && t.closable) { e.preventDefault(); void actions.close(t.key) } }}
                  onContextMenu={(e) => { e.preventDefault(); openMenu(t.key, e.clientX, e.clientY) }}
                  className={cn('flex cursor-pointer select-none items-center gap-1.5 rounded-md py-1.5 pl-3 text-sm outline-none',
                    t.closable ? 'pr-1' : 'pr-3', 'focus-visible:ring-2 focus-visible:ring-primary-500',
                    active ? 'font-medium text-primary-700' : 'text-neutral-600')}>
                  {Icon && <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />}
                  <span className="max-w-[160px] truncate">{t.title}</span>
                  {t.dirty && <span aria-label="unsaved changes" className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning-500" />}
                  {sheetOpen && <span aria-hidden data-sheet-dot className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary-600" />}
                </div>
                {t.closable && (
                  // For the mouse: a tab closes from the keyboard with Delete, or its menu (Shift+F10) — what a screen reader says.
                  <button type="button" tabIndex={-1} aria-hidden title={`Close ${t.title}`} data-close-tab={t.key} onClick={() => { void actions.close(t.key) }}
                    className="mr-1 rounded p-0.5 text-neutral-400 opacity-60 hover:bg-neutral-200 hover:text-neutral-700 group-hover:opacity-100">
                    <X aria-hidden className="h-3 w-3" />
                  </button>
                )}
              </div>
            )
          })}
        </div>
        <AllTabs tabs={tabs} activeKey={activeKey} routes={routes} recent={recent} actions={actions} />
      </div>
      {notice && (
        <div role="status" className={cn('flex items-center gap-3 border-t px-4 py-1.5 text-xs',
          notice.tone === 'warning' ? 'border-warning-200 bg-warning-50 text-warning-800' : 'border-primary-100 bg-primary-50 text-primary-800')}>
          <span>{notice.text}</span>
          {notice.reopen && <button type="button" className="font-medium underline" onClick={() => actions.reopen(notice.reopen!.path)}>Reopen</button>}
          <button type="button" aria-label="Dismiss" className="ml-auto rounded p-0.5 hover:bg-white/60" onClick={() => tabSession.notify(null)}>
            <X aria-hidden className="h-3 w-3" />
          </button>
        </div>
      )}
      {menu && menuTab && (
        <ContextMenuAt x={menu.x} y={menu.y} label={`${menuTab.title} tab menu`} onClose={() => { setMenu(null); focusTab(menuTab.key) }}
          items={[
            { label: 'Refresh', onSelect: () => actions.refresh(menuTab.key) },
            { label: 'Close', hint: 'Delete', disabled: !menuTab.closable, separated: true, onSelect: () => actions.close(menuTab.key) },
            { label: 'Close others', onSelect: () => actions.closeOthers(menuTab.key) },
            { label: 'Close to the right', disabled: at === tabs.length - 1, onSelect: () => actions.closeToTheRight(menuTab.key) },
            { label: 'Copy link', separated: true, disabled: !menuTab.path, onSelect: () => actions.copyLink(menuTab.key) },
            { label: 'Reopen closed tab', disabled: recent.length === 0, disabledReason: 'None closed yet', onSelect: () => actions.reopen() },
          ]} />
      )}
    </div>
  )
}

/** All tabs ▾: every open tab with a search — lists, then records — and the tabs closed lately (Reopen); the cap. */
function AllTabs({ tabs, activeKey, routes, recent, actions }: {
  tabs: TabMeta[]; activeKey: string; routes: RouteDef[]; recent: { path: string; title: string }[]; actions: ReturnType<typeof useTabActions>
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const cap = useTabSession((s) => s.cap)
  const isRecord = useMemo(() => recordTabTest(routes), [routes])
  const match = (title: string) => title.toLowerCase().includes(q.trim().toLowerCase())
  const lists = tabs.filter((t) => !isRecord(t) && match(t.title))
  const records = tabs.filter((t) => isRecord(t) && match(t.title))
  const closed = recent.filter((r) => match(r.title))
  const go = (fn: () => void) => { fn(); setOpen(false); setQ('') }
  const row = (t: TabMeta) => (
    <li key={t.key}>
      <button type="button" onClick={() => go(() => actions.select(t.key))} aria-current={t.key === activeKey || undefined}
        className={cn('flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-neutral-100', t.key === activeKey && 'bg-neutral-100 font-medium')}>
        <span className="min-w-0 flex-1 truncate">{t.title}</span>
        {t.dirty && <span className="text-xs text-warning-700">● not saved</span>}
        {t.key === activeKey && <span className="text-xs text-neutral-500">shown</span>}
      </button>
    </li>
  )
  const group = (label: string, items: React.ReactNode[]) => items.length > 0 && (
    <section aria-label={label}>
      <h3 className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-neutral-500">{label}</h3>
      <ul>{items}</ul>
    </section>
  )
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ('') }} align="end" className="w-80"
      trigger={
        <button type="button" aria-label={`All tabs (${tabs.length})`} aria-haspopup="dialog"
          className="flex shrink-0 items-center gap-1 self-center rounded-md border border-neutral-200 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50">
          All tabs <span className="tabular-nums text-neutral-500">{tabs.length}</span><ChevronDown aria-hidden className="h-3 w-3" />
        </button>
      }>
      <div role="group" aria-label="All tabs" className="flex max-h-[70vh] flex-col">
        <input autoFocus aria-label="Find a tab" placeholder="Find a tab…" value={q} onChange={(e) => setQ(e.target.value)}
          className="mb-1 h-8 rounded-md border border-neutral-200 px-2 text-sm outline-none focus:border-primary-500" />
        <div className="min-h-0 overflow-y-auto">
          {group('Lists', lists.map(row))}
          {group('Records', records.map(row))}
          {group('Recently closed', closed.map((r) => (
            <li key={r.path}>
              <button type="button" onClick={() => go(() => actions.reopen(r.path))}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-neutral-700 hover:bg-neutral-100">
                <span className="min-w-0 flex-1 truncate">{r.title}</span><span className="text-xs text-primary-700">Reopen</span>
              </button>
            </li>
          )))}
          {lists.length + records.length + closed.length === 0 && <p className="px-2 py-3 text-sm text-neutral-500">No tab matches.</p>}
        </div>
        <div role="group" aria-label="Record tabs kept open" className="mt-1 flex items-center gap-1 border-t border-neutral-100 px-2 pt-2 text-xs text-neutral-600">
          <span className="mr-1">Keep at most</span>
          {RECORD_TAB_CAPS.map((n) => (
            <button key={n} type="button" aria-pressed={cap === n} onClick={() => tabSession.setCap(n)}
              className={cn('rounded border px-1.5 py-0.5 tabular-nums', cap === n ? 'border-primary-500 bg-primary-50 text-primary-700' : 'border-neutral-200 hover:bg-neutral-50')}>{n}</button>
          ))}
          <span className="ml-1">record tabs open</span>
        </div>
      </div>
    </Popover>
  )
}
