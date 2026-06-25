/**
 * PortalChromeLayout (Finance) — content-only page container.
 *
 * In the standalone Finance app the sidebar/header/tabs come from the AppLayout
 * tab shell, so this no longer renders chrome. It keeps the same props the
 * finance/budget pages already pass (title/subtitle/headerActions/flushBody/
 * scrollableBody) and just lays out the page body, so those pages didn't need
 * editing during the Portal → Finance migration.
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PortalChromeLayoutProps {
  /** Ignored here — the tab shell tracks the active tab. Kept for call-site compat. */
  activeKey?: string
  title?: string
  subtitle?: string
  headerActions?: ReactNode
  children: ReactNode
  scrollableBody?: boolean
  flushBody?: boolean
}

export function PortalChromeLayout({
  title,
  subtitle,
  headerActions,
  children,
  flushBody = false,
}: PortalChromeLayoutProps) {
  // Full-bleed body (iframe pages) — no padding/max-width, fill the tab area.
  if (flushBody) {
    return <div className="h-full">{children}</div>
  }

  return (
    <div className="mx-auto max-w-[1440px] p-4 md:p-6">
      {(title || headerActions) && (
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="min-w-0">
            {title && <h1 className="text-2xl font-bold text-neutral-900">{title}</h1>}
            {subtitle && <p className="mt-1 text-sm text-neutral-500">{subtitle}</p>}
          </div>
          {headerActions && <div className={cn('flex shrink-0 items-center gap-2')}>{headerActions}</div>}
        </div>
      )}
      {children}
    </div>
  )
}
