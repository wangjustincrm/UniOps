import { useCallback, useContext, useRef, useState } from 'react'
import { QueryClientContext, type QueryKey } from '@tanstack/react-query'
import { errorText } from '../resource/businessError'
import { useNotices, type NoticeTone } from './notices'

/** What a finished action says: a text (success), or a text with its own tone (e.g. a warning when only
 *  part of it worked); null says nothing. */
export type ActionDone = string | { tone: NoticeTone; text: string } | null

export interface ActionOptions {
  /** What to say when it worked (the status row of the list or sheet it is in); a function reads the result. */
  done?: ActionDone | ((result: unknown) => ActionDone)
  /** Business error codes → words. */
  errorMessages?: Record<string, string>
  /** Before the error text, e.g. "Download failed". */
  failPrefix?: string
  /** Read again what is on screen after it worked (default): false for what changes nothing (a download). */
  refreshes?: boolean
  /** Read again only these (query keys, e.g. queryKeys.detail(client, id)), not everything on screen: for an
   *  action whose effect is local, so other open tabs' heavy lists are not fetched again. */
  invalidates?: QueryKey[]
  /** Then, after it worked and was said: e.g. close a sheet, show a preview from the result. */
  after?: (result: unknown) => unknown | Promise<unknown>
}

/** What follows an action that worked, the same for a button, a menu item and a confirmation: read again
 *  what it changed, say it, then the caller's `after`. */
export function useAfterAction() {
  const notices = useNotices()
  const qc = useContext(QueryClientContext)  // none outside the app (a test, an embedded page): nothing to refresh
  return useCallback(async (result: unknown, opts: Pick<ActionOptions, 'done' | 'refreshes' | 'invalidates' | 'after'>) => {
    const { done, refreshes = true, invalidates, after } = opts
    if (invalidates) for (const queryKey of invalidates) void qc?.invalidateQueries({ queryKey })
    else if (refreshes) void qc?.invalidateQueries()  // what it changed shows at once, wherever it is on screen
    const said = typeof done === 'function' ? done(result) : done
    if (said) notices.notify(typeof said === 'string' ? { tone: 'success', text: said } : said)
    await after?.(result)
  }, [notices, qc])
}

/** The one way an action runs (2026-11-10): ActionButton, RowActions, DownloadButton and a
 *  record sheet's More menu all use it. It never runs twice at once (a ref, so two clicks in
 *  one tick count once), says the result in the status row of the list or sheet it is in, and
 *  never leaves a rejected promise behind: outside any status row the failure is logged. */
export function useAction(opts: ActionOptions = {}) {
  const notices = useNotices()
  const afterAction = useAfterAction()
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  const { done: baseDone, errorMessages: baseMessages, failPrefix: basePrefix, refreshes = true, invalidates, after } = opts
  /** `call` overrides the hook's options for this run (a menu runs several kinds of item). */
  const run = useCallback(async (action: () => unknown | Promise<unknown>, call: ActionOptions = {}): Promise<boolean> => {
    const errorMessages = call.errorMessages ?? baseMessages
    const failPrefix = call.failPrefix ?? basePrefix
    if (inFlight.current) return false
    inFlight.current = true
    setPending(true)
    try {
      const result = await action()
      await afterAction(result, { done: call.done ?? baseDone, refreshes: call.refreshes ?? refreshes,
        invalidates: call.invalidates ?? invalidates, after: call.after ?? after })
      return true
    } catch (e) {
      const text = errorText(e, errorMessages)
      if (notices.inScope) notices.notify({ tone: 'error', text: failPrefix ? `${failPrefix}: ${text}` : text })
      else console.error(failPrefix ?? 'Action failed', e)
      return false
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }, [baseDone, baseMessages, basePrefix, notices, refreshes, invalidates, after, afterAction])
  return { run, pending }
}
