import { useCallback, useContext } from 'react'
import { useCurrentUnsavedScope, whenDiscarded } from '../primitives/unsaved'
import { requestOpen, requestView, type ViewRequest } from '../resource/viewRequests'
import { OpenRecordNavigate } from './openRecordContext'
import { RecordSheetHostContext } from './recordSheetHost'

// One way to go from here to another record or list (2026-11-10). It asks about unsaved input
// here first, closes the record sheet it is called from (a sheet stays at the page body: left
// open, it would cover the page it goes to), asks the target list to open the record or the
// view, then goes there. The app hands in its router's navigate, so platform-ui needs no router.

function useLeave() {
  const go = useContext(OpenRecordNavigate)
  const host = useContext(RecordSheetHostContext)
  const scope = useCurrentUnsavedScope()
  return useCallback((to: string, request: () => void): void => {
    whenDiscarded(scope, () => {
      host?.close()
      request()
      go?.(to)
    })
  }, [go, host, scope])
}

/** `open('/orders', id, tab?)`: the record in its list's sheet. */
export function useOpenRecord() {
  const leave = useLeave()
  return useCallback((page: string, id: number, tab?: string) => leave(page, () => requestOpen(page, id, tab)), [leave])
}

/** `openView('/documents', 'documents', view)`: a list with a view (a saved one's key, or filters). */
export function useOpenView() {
  const leave = useLeave()
  return useCallback((page: string, resource: string, view: ViewRequest) => leave(page, () => requestView(resource, view)), [leave])
}

/** `goTo('/deliveries')`: another page, leaving this one the same way (unsaved input, the open sheet). */
export function useGoTo() {
  const leave = useLeave()
  return useCallback((page: string) => leave(page, () => {}), [leave])
}
