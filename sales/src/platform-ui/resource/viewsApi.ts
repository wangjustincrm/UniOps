import type { HttpClient } from './ResourceClient'
import type { ViewState } from './viewState'

// `/views` (design §13.19): the signed-in user's own saved views of a list.

export interface SavedView {
  id: number
  resource: string
  name: string
  state: ViewState
  is_default: boolean
  sort: number
  version: number
  /** Saved by an older version, or naming a field the list no longer has. */
  stale: boolean
}

export interface ViewsClient {
  list: (resource: string) => Promise<SavedView[]>
  create: (resource: string, name: string, state: ViewState) => Promise<SavedView>
  update: (id: number, data: { name?: string; state?: ViewState }, version?: number) => Promise<SavedView>
  remove: (id: number) => Promise<void>
  setDefault: (id: number, on: boolean) => Promise<SavedView>
}

export function createViewsClient(http: HttpClient & { delete: <T>(path: string) => Promise<T> }): ViewsClient {
  return {
    list: (resource) => http.get<SavedView[]>(`/views?resource=${encodeURIComponent(resource)}`),
    create: (resource, name, state) => http.post<SavedView>('/views', { resource, name, state }),
    update: (id, data, version) => http.put<SavedView>(version === undefined ? `/views/${id}` : `/views/${id}?version=${version}`, data),
    remove: (id) => http.delete<void>(`/views/${id}`),
    setDefault: (id, on) => http.post<SavedView>(`/views/${id}/default?on=${on}`),
  }
}

/** The resource key of a list: its client's path without the leading "/". */
export const resourceKey = (basePath: string): string => basePath.replace(/^\/+/, '')
