import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { useTabStore, useTabStoreApi } from './TabStoreContext'
import { resolveIcon } from '../lib/icon'
import { cn } from '../lib/cn'

export function TabBar() {
  const tabs = useTabStore((s) => s.tabs)
  const activeKey = useTabStore((s) => s.activeKey)
  const api = useTabStoreApi()
  const navigate = useNavigate()

  const select = (key: string, path?: string) => {
    api.getState().setActive(key)
    if (path) navigate(path)
  }

  const close = (e: React.MouseEvent, key: string) => {
    e.stopPropagation()
    const tab = api.getState().tabs.find((t) => t.key === key)
    if (tab?.dirty && !window.confirm('This tab has unsaved changes. Close anyway?')) return
    api.getState().closeTab(key)
    // After close, sync the URL to whatever became active.
    const next = api.getState().tabs.find((t) => t.key === api.getState().activeKey)
    if (next?.path) navigate(next.path)
  }

  return (
    <div className="flex h-10 shrink-0 items-stretch gap-1 overflow-x-auto border-b border-neutral-200 bg-white px-2">
      {tabs.map((t) => {
        const Icon = resolveIcon(t.icon)
        const active = t.key === activeKey
        return (
          <button
            key={t.key}
            onClick={() => select(t.key, t.path)}
            className={cn(
              'group flex items-center gap-1.5 self-center rounded-md px-3 py-1.5 text-sm transition-colors',
              active
                ? 'bg-primary-50 text-primary-700 font-medium'
                : 'text-neutral-600 hover:bg-neutral-50',
            )}
          >
            {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
            <span className="max-w-[160px] truncate">{t.title}</span>
            {t.dirty && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning-500" />}
            {t.closable && (
              <span
                role="button"
                tabIndex={-1}
                aria-label={`Close ${t.title}`}
                onClick={(e) => close(e, t.key)}
                className="ml-1 rounded p-0.5 text-neutral-400 opacity-60 hover:bg-neutral-200 hover:text-neutral-700 group-hover:opacity-100"
              >
                <X className="h-3 w-3" />
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
