import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, type ReactNode } from 'react'
import { HelpProvider, PLATFORM_HELP, type HelpEntry } from '@/platform-ui'
import { hasSession } from '@/lib/api'
import { PAGE_HELP } from './pageHelp'
import { fetchStatusHelp } from './statusHelpApi'

// The Sales help (the user, 2026-11-16). What a Status, an On Hold reason or a Detail means comes
// from the API (sales-api app/domain/status_help.py, beside the rules); texts only the screens need
// are a layer (pageHelp.ts). Ids: status:<Status>, hold:<reason>, detail:<Detail>, term:<glossary term>, page:<page>.

declare module '@/platform-ui/help/types' {
  interface HelpRegistry {
    [status: `status:${string}`]: true
    [hold: `hold:${string}`]: true
    [detail: `detail:${string}`]: true
    [term: `term:${string}`]: true
    [page: `page:${string}`]: true
  }
}



/** The text layers: the component library's (the list tools), then Sales' screens'; a later one overrides by id. */
const LAYERS = [PLATFORM_HELP, PAGE_HELP]

const PART = { status: 'statuses', hold: 'holds', detail: 'details', term: 'terms' } as const
/** What a search result says it is. */
const KIND = { status: 'order status', hold: 'On Hold reason', detail: 'order detail', term: 'term' } as const

export function SalesHelpProvider({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ['sales', 'help', 'texts'], queryFn: fetchStatusHelp, staleTime: Infinity, gcTime: Infinity,
    enabled: hasSession(), retry: 1 })
  const data = q.data
  const source = useCallback((id: string): HelpEntry | undefined => {
    const [kind, ...rest] = id.split(':')
    const part = PART[kind as keyof typeof PART]
    const name = rest.join(':')
    const t = part && data?.[part]?.[name]
    return t ? { title: name, body: t.body } : undefined
  }, [data])
  // A missing text shows no help; while developing, say which (the API's texts are checked by its tests).
  const warned = useRef(new Set<string>())  // once per id, not once per row and render
  const onMissing = useCallback((id: string) => {
    if (!import.meta.env.DEV || !data || warned.current.has(id)) return
    warned.current.add(id)
    console.warn(`[help] no text for "${id}"`)
  }, [data])
  // The API's texts, for the help panel's search (the layers are listed by the provider).
  const catalog = useCallback(() => [
    ...(Object.keys(PART) as (keyof typeof PART)[]).flatMap((kind) =>
      Object.entries(data?.[PART[kind]] ?? {}).map(([name, t]) => ({ id: `${kind}:${name}`, entry: { title: `${name} — ${KIND[kind]}`, body: t.body } }))),
  ], [data])
  return <HelpProvider layers={LAYERS} source={source} onMissing={onMissing} catalog={catalog}>{children}</HelpProvider>
}
