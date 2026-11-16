import { useQuery } from '@tanstack/react-query'
import { useCallback, type ReactNode } from 'react'
import { HelpProvider, type HelpEntry } from '@/platform-ui'
import { hasSession } from '@/lib/api'
import { fetchStatusHelp } from './statusHelpApi'

// The Sales help (the user, 2026-11-16). What a Status, an On Hold reason or a Detail means comes
// from the API (sales-api app/domain/status_help.py, beside the rules); texts only the screens need
// live in UI_HELP below. Ids: status:<Status>, hold:<reason>, detail:<Detail>, and UI_HELP's keys.

declare module '@/platform-ui/help/types' {
  interface HelpRegistry {
    [status: `status:${string}`]: true
    [hold: `hold:${string}`]: true
    [detail: `detail:${string}`]: true
  }
}


/** Texts that belong to a screen, not to the business rules (none yet: page panels come in round 3). */
const UI_HELP: Record<string, HelpEntry> = {}

const PART = { status: 'statuses', hold: 'holds', detail: 'details' } as const

export function SalesHelpProvider({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ['sales', 'help', 'order-statuses'], queryFn: fetchStatusHelp, staleTime: Infinity, gcTime: Infinity,
    enabled: hasSession(), retry: 1 })
  const data = q.data
  const source = useCallback((id: string): HelpEntry | undefined => {
    if (UI_HELP[id]) return UI_HELP[id]
    const [kind, ...rest] = id.split(':')
    const part = PART[kind as keyof typeof PART]
    const name = rest.join(':')
    const t = part && data?.[part]?.[name]
    return t ? { title: name, body: t.body } : undefined
  }, [data])
  // A missing text shows no help; while developing, say which (the API's texts are checked by its tests).
  const onMissing = useCallback((id: string) => { if (import.meta.env.DEV && data) console.warn(`[help] no text for "${id}"`) }, [data])
  return <HelpProvider source={source} onMissing={onMissing}>{children}</HelpProvider>
}
