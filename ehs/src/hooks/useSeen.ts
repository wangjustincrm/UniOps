/**
 * Round 12, ACT-01/02/03/04 and INC-08: what is new to me.
 *
 * One query behind every flag — the menu's counts, the "New" tile on My
 * Actions and the chip on each card — so none of them can disagree with the
 * others. Seen is kept per person on the server (app/services/seen.py).
 */
import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export interface NewIncident {
  id: string; incident_no: string; title: string; form_kind: string
  submitted_at: string; location_path: string | null
}

export interface SeenSummary {
  my_actions: number
  task_ids: string[]
  action_ids: string[]
  news: number
  incidents: NewIncident[]
  feedback_answered: number
}

export const SEEN_KEY = ['seen'] as const

export function useSeen() {
  return useQuery({
    queryKey: SEEN_KEY,
    queryFn: () => api.get<SeenSummary>('/api/v1/seen'),
    // A hint, not a register: polled gently, like the feedback count was.
    refetchInterval: 120_000,
    staleTime: 30_000,
  })
}

/**
 * The record pages a task or a new incident opens, by path. Opening one of
 * these — from My Actions, from an email or from anywhere else — is what
 * clears its flag, so this is read off the address rather than wired into
 * each page, where one would be forgotten.
 */
const OPENS: [RegExp, string][] = [
  [/^\/actions\/([0-9a-f-]{36})$/, 'ehs_act'],
  [/^\/incidents\/([0-9a-f-]{36})(\/investigate)?$/, 'ehs_inc'],
  [/^\/hazards\/([0-9a-f-]{36})$/, 'ehs_haz'],
  [/^\/permits\/([0-9a-f-]{36})$/, 'ehs_ptw'],
  [/^\/documents\/([0-9a-f-]{36})$/, 'ehs_doc'],
  [/^\/forms\/submissions\/([0-9a-f-]{36})$/, 'ehs_sub'],
  [/^\/jhsc\/recommendations\/([0-9a-f-]{36})$/, 'ehs_jhsc'],
]

export function useMarkOpened(pathname: string) {
  const queryClient = useQueryClient()
  useEffect(() => {
    for (const [re, docType] of OPENS) {
      const m = re.exec(pathname)
      if (!m) continue
      api.post('/api/v1/seen/opened', { doc_type: docType, doc_id: m[1] })
        .then(() => queryClient.invalidateQueries({ queryKey: SEEN_KEY }))
        // A flag that fails to clear is a nuisance, not a fault worth a page.
        .catch(() => undefined)
      return
    }
  }, [pathname, queryClient])
}

/** Clear one of the per-page flags (news on My Actions, answers on Feedback). */
export function useMarkRead(what: 'news' | 'feedback', when: boolean) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!when) return
    api.post(`/api/v1/seen/${what}`)
      .then(() => queryClient.invalidateQueries({ queryKey: SEEN_KEY }))
      .catch(() => undefined)
  }, [what, when, queryClient])
}

/** A card on My Actions was pressed: its task is no longer new. */
export function markTaskOpened(taskId: string) {
  return api.post('/api/v1/seen/task', { task_id: taskId }).catch(() => undefined)
}
