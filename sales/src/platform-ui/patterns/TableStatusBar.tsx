import { useState, type ReactNode } from 'react'
import { AlertTriangle, Bell, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { IconButton } from '../primitives/IconButton'
import { IconPopover } from '../primitives/IconPopover'
import { formatTime } from '../primitives/format'
import { cn } from '../lib/cn'
import { useNoticeSnapshot, type NoticeStore, type NoticeTone } from './notices'

const ICON: Record<NoticeTone, ReactNode> = {
  info: <Info aria-hidden className="h-3.5 w-3.5 shrink-0 text-primary-600" />,
  success: <CheckCircle2 aria-hidden className="h-3.5 w-3.5 shrink-0 text-success-600" />,
  warning: <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0 text-warning-600" />,
  error: <XCircle aria-hidden className="h-3.5 w-3.5 shrink-0 text-danger-600" />,
}
// A tinted chip per tone (tokens only; dark text on a light tint keeps AA contrast).
const CHIP: Record<NoticeTone, string> = {
  info: 'border-primary-200 bg-primary-50 text-primary-800',
  success: 'border-success-200 bg-success-50 text-success-800',
  warning: 'border-warning-200 bg-warning-50 text-warning-800',
  error: 'border-danger-200 bg-danger-50 text-danger-700',
}

/** THE status row of a list or a record sheet (the user, 2026-11-03): a fixed
 *  row under the table (or the sheet's tabs) that always takes its space — the
 *  current notice as a chip tinted by its tone (icon, text, its action, ×;
 *  "+N" when more sticky ones are open; read aloud: politely, an error at once;
 *  info and success fade after ~4 s, a warning or an error stays until closed), a bell with the unseen count opening the
 *  session's history, and on the right what the caller adds (the row count and
 *  the pages). Under ~480 px the right part wraps below; the notice keeps its row. */
export function TableStatusBar({ store, right, className }: { store: NoticeStore; right?: ReactNode; className?: string }) {
  const { current, history, unseen, moreSticky } = useNoticeSnapshot(store)
  const [open, setOpen] = useState(false)
  const error = current?.tone === 'error'
  return (
    <div data-status-bar className={cn('flex flex-wrap items-center gap-x-3 border-t border-neutral-200 bg-white px-3 text-sm', className)}>
      <div className="flex h-9 min-w-0 flex-1 basis-60 items-center gap-2">
        <div aria-live={error ? 'assertive' : 'polite'} data-notice={current?.tone ?? undefined}
          className="flex min-w-0 flex-1 items-center gap-1.5">
          {current && (
            <div key={current.id} data-tone={current.tone}
              className={cn('flex h-7 min-w-0 max-w-full items-center gap-1.5 rounded-full border pl-2 pr-0.5', CHIP[current.tone],
                current.sticky ? 'animate-[platform-notice-in_150ms_ease-out] motion-reduce:animate-none'
                  : 'animate-[platform-notice-life_4s_ease-in-out_forwards] motion-reduce:animate-none')}>
              {ICON[current.tone]}
              <span title={current.text} className="truncate text-xs font-medium">{current.text}</span>
              {current.action && (
                <button type="button" onClick={current.action.onClick}
                  className="shrink-0 rounded-full px-1.5 text-xs font-semibold underline underline-offset-2 hover:no-underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                  {current.action.label}
                </button>
              )}
              {moreSticky > 0 && (
                <button type="button" aria-label={`${moreSticky} more open: show the notifications`} onClick={() => { setOpen(true); store.markSeen() }}
                  className="shrink-0 rounded-full bg-white/70 px-1.5 text-[11px] font-semibold tabular-nums focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                  +{moreSticky}
                </button>
              )}
              <IconButton label="Dismiss" icon={<X aria-hidden className="h-3.5 w-3.5" />} onClick={() => store.dismiss(current.id)}
                className="h-6 w-6 shrink-0 rounded-full text-current opacity-70 hover:bg-white/60 hover:opacity-100" />
            </div>
          )}
        </div>
        <IconPopover label="Notifications" icon={<Bell aria-hidden className="h-3.5 w-3.5" />} badge={unseen || undefined} open={open}
          onOpenChange={(o) => { setOpen(o); if (o) store.markSeen() }} align="end" className="w-80">
          <div className="flex items-center justify-between pb-2">
            <h3 className="text-sm font-semibold text-neutral-800">Notifications</h3>
            <button type="button" onClick={() => store.clearHistory()} disabled={history.length === 0}
              className="rounded px-1.5 text-xs text-primary-700 hover:underline disabled:opacity-40">Clear</button>
          </div>
          {history.length === 0 ? <p className="py-3 text-center text-xs text-neutral-400">Nothing yet in this session</p> : (
            <ol aria-label="Notification history" className="flex max-h-72 flex-col divide-y divide-neutral-100 overflow-y-auto">
              {history.map((n) => (
                <li key={n.id} className="flex items-start gap-2 py-1.5 text-xs">
                  <span className="mt-0.5">{ICON[n.tone]}</span>
                  <span className="min-w-0 flex-1 break-words text-neutral-700">{n.text}</span>
                  <span className="shrink-0 tabular-nums text-neutral-400">{formatTime(n.at)}</span>
                </li>
              ))}
            </ol>
          )}
        </IconPopover>
      </div>
      {right && <div className="flex min-h-9 items-center justify-end max-[480px]:w-full">{right}</div>}
    </div>
  )
}
