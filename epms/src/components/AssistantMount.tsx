/**
 * Wires the shared Assistant panel to EPMS.
 *
 * The panel itself lives in @uniops/shell and knows nothing about this app — it
 * takes an `ask` function, which is what keeps the shared package free of any
 * API client or auth store. This file is the whole of the EPMS half.
 *
 * Page context is what makes "why can't I submit this?" resolvable without the
 * person typing a document number. It is derived from the URL rather than from
 * the tab store on purpose: the tab store is absent in the iframe-embedded mode,
 * and a global overlay must never be the reason a page fails to render.
 */
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Assistant, type AssistantContext, type AssistantReply, type AssistantSavedApi, type AssistantSavedList } from '@uniops/shell'
import { api } from '@/lib/api'

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
import { useAuthStore } from '@/stores/auth.store'

/**
 * Route segment → the doc_type the backend knows. Agreements and agreement
 * receipts were missing, so on those pages the panel did not know a document
 * was open at all (ISS-0068).
 */
const DOC_TYPE_BY_SEGMENT: Record<string, string> = {
  pr: 'pr',
  po: 'po',
  gr: 'gr',
  invoice: 'invoice',
  invoices: 'invoice',
  pa: 'pa',
  agreements: 'agreement',
  receipts: 'agreement_receipt',
}

/** URL → what document the person is looking at, if any. */
export function deriveContext(pathname: string): AssistantContext {
  const ctx: AssistantContext = { app: 'epms', route: pathname }
  // /pr/<id>, /po/<id>, /gr/<id>… — the id is only useful when it is an id, not
  // a sub-route like /pr/new or /pr/list.
  const m = pathname.match(
    /^\/(pr|po|gr|invoice|invoices|pa|agreements|receipts)\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
  )
  if (m) {
    ctx.doc_type = DOC_TYPE_BY_SEGMENT[m[1].toLowerCase()]
    ctx.doc_id = m[2]
  }
  return ctx
}

async function ask(
  message: string,
  context: AssistantContext,
  history: { role: 'user' | 'assistant'; text: string }[]
): Promise<AssistantReply> {
  return api.post<AssistantReply>('/assistant/chat', { message, context, history })
}

/**
 * Download an answer's query as a workbook.
 *
 * Goes through api.download rather than a plain link so the request carries the
 * auth header; the sandbox a plain <a download> would need is not available
 * cross-origin, and the filename comes from the response.
 */
async function exportQuery(
  query: Record<string, unknown>,
  question: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/assistant/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${useAuthStore.getState().token ?? ''}`,
    },
    body: JSON.stringify({ ...query, question }),
  })
  if (!res.ok) throw new Error(`Export failed (${res.status})`)

  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const named = /filename="([^"]+)"/.exec(disposition)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = named?.[1] ?? 'report.xlsx'
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** What this person can actually be answered about, straight from the ontology. */
async function describeScope(): Promise<string[]> {
  // Only what the EPMS chat queries: Finance's AP copy is hidden here (ISS-0015).
  const res = await api.get<{ entities: { label: string }[] }>('/assistant/schema?app=epms')
  // Labels read "Purchase Order (PO) — the order placed with a vendor"; the part
  // before the dash is the name, which is all that belongs in a one-line summary.
  return res.entities.map((e) => e.label.split('—')[0].trim())
}

/**
 * The person's marked questions. Kept by EPMS against their own user, so the
 * same list shows up in every app that has the panel.
 */
const saved: AssistantSavedApi = {
  list: () => api.get<AssistantSavedList>('/assistant/saved'),
  add: (question) => api.post<AssistantSavedList>('/assistant/saved', { question }),
  remove: (id) => api.delete<AssistantSavedList>(`/assistant/saved/${id}`),
}

/**
 * How far the round chat button must rise to clear a bar pinned to the bottom
 * of the window — the review bar on PR / PO / PA / agreement detail pages
 * (ISS-0227: the button sat on its Approve button, showing "Appro"). Only bars
 * that reach the button's corner count. `bars` are the bars' rectangles.
 */
export function bottomBarLift(
  bars: { top: number; right: number; height: number }[],
  viewport: { width: number; height: number },
): number {
  // The button is 48px wide, 24px from the right edge (right-6 w-12).
  const buttonLeft = viewport.width - 24 - 48
  let lift = 0
  for (const b of bars) {
    if (b.height <= 0 || b.right <= buttonLeft) continue
    lift = Math.max(lift, Math.ceil(viewport.height - b.top))
  }
  return lift
}

/**
 * Keeps --assistant-lift on <html> equal to the height of whatever bottom bar
 * is on screen. Read from the DOM rather than wired through every page so the
 * pages that own the bars need no change; the bars are Tailwind's
 * `fixed bottom-0`.
 */
function useBottomBarLift() {
  useEffect(() => {
    let frame = 0
    const measure = () => {
      frame = 0
      const bars = Array.from(document.querySelectorAll<HTMLElement>('.fixed.bottom-0'))
        .map((el) => el.getBoundingClientRect())
      const lift = bottomBarLift(bars, { width: window.innerWidth, height: window.innerHeight })
      document.documentElement.style.setProperty('--assistant-lift', `${lift}px`)
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure) }
    measure()
    const observer = new MutationObserver(schedule)
    observer.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', schedule)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', schedule)
      if (frame) cancelAnimationFrame(frame)
      document.documentElement.style.removeProperty('--assistant-lift')
    }
  }, [])
}

export function AssistantMount() {
  const { pathname } = useLocation()
  // Same field AppLayout gates on, rather than a second definition of
  // "signed in" that could drift from it.
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  useBottomBarLift()

  // No panel on the login screen: there is nothing to ask about, and every call
  // it could make would 401.
  if (!isAuthenticated) return null

  // The button sits 24px above a bottom bar when one is shown, else 24px
  // above the window edge as before.
  return (
    <Assistant
      ask={ask}
      describeScope={describeScope}
      exportQuery={exportQuery}
      saved={saved}
      context={deriveContext(pathname)}
      className="bottom-[calc(var(--assistant-lift,0px)+1.5rem)]"
    />
  )
}
