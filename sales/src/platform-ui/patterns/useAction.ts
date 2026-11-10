import { useCallback, useRef, useState } from 'react'
import { errorText } from '../resource/businessError'
import { useNotices } from './notices'

export interface ActionOptions {
  /** What to say when it worked (the status row of the list or sheet it is in). */
  done?: string | ((result: unknown) => string | null)
  /** Business error codes → words. */
  errorMessages?: Record<string, string>
  /** Before the error text, e.g. "Download failed". */
  failPrefix?: string
}

/** The one way an action runs (2026-11-10): ActionButton, RowActions, DownloadButton and a
 *  record sheet's More menu all use it. It never runs twice at once (a ref, so two clicks in
 *  one tick count once), says the result in the status row of the list or sheet it is in, and
 *  never leaves a rejected promise behind: outside any status row the failure is logged. */
export function useAction(opts: ActionOptions = {}) {
  const notices = useNotices()
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  const { done: baseDone, errorMessages: baseMessages, failPrefix: basePrefix } = opts
  /** `call` overrides the hook's options for this run (a menu runs several kinds of item). */
  const run = useCallback(async (action: () => unknown | Promise<unknown>, call: ActionOptions = {}): Promise<boolean> => {
    const done = call.done ?? baseDone
    const errorMessages = call.errorMessages ?? baseMessages
    const failPrefix = call.failPrefix ?? basePrefix
    if (inFlight.current) return false
    inFlight.current = true
    setPending(true)
    try {
      const result = await action()
      const said = typeof done === 'function' ? done(result) : done
      if (said) notices.notify({ tone: 'success', text: said })
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
  }, [baseDone, baseMessages, basePrefix, notices])
  return { run, pending }
}
