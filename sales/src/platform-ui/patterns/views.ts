import { createContext, useContext, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useListMeta, type ListMeta, type SystemView } from '../resource/listMeta'
import type { Filter, QuerySpec } from '../resource/querySpec'
import { takeViewRequest, useViewRequest } from '../resource/viewRequests'
import { resourceKey, type SavedView, type ViewsClient } from '../resource/viewsApi'
import {
  layoutOf, normalizeView, sameView, specOf, viewStateOf, type Layout, type ViewState,
} from '../resource/viewState'

// Saved views of a list (design §13.19). The app provides the views client
// once (ViewsProvider); a list without it — or opened with views off — has
// no picker and keeps its own spec and layout.

export const ViewsContext = createContext<ViewsClient | null>(null)

export type CurrentView = { kind: 'system'; key: string; name: string } | { kind: 'mine'; id: number; name: string }

export interface ViewPickerModel {
  system: SystemView[]
  mine: SavedView[]
  current: CurrentView | null
  dirty: boolean
  stale: boolean
  busy: boolean
  error: unknown
  select: (v: CurrentView) => void
  save: () => Promise<void>
  saveAs: (name: string) => Promise<void>
  reset: () => void
  rename: (id: number, name: string) => Promise<void>
  remove: (id: number) => Promise<void>
  setDefault: (id: number, on: boolean) => Promise<void>
}

export interface ListView {
  spec: QuerySpec
  setSpec: (spec: QuerySpec) => void
  layout: Layout
  setLayout: (layout: Layout) => void
  /** False until the opening view is applied: lists wait, so they fetch once. */
  ready: boolean
  /** Absent when the list has no views. */
  picker?: ViewPickerModel
  /** Spread into QueryTable: spec, layout and the picker. */
  tableProps: {
    spec: QuerySpec
    onSpecChange: (spec: QuerySpec) => void
    layout: Layout
    onLayoutChange: (layout: Layout) => void
    picker?: ViewPickerModel
  }
}

const fullState = (partial: Partial<ViewState>, pageSize: number): ViewState => normalizeView({ pageSize, ...partial })

/** The list's query + layout, and its views when the app provides them.
 *  `fixed` filters (a list inside another record) are always kept and turn
 *  views off. */
export function useListView(
  resource: { basePath: string; meta: () => Promise<ListMeta> },
  { pageSize = 50, fixed = [] }: { pageSize?: number; fixed?: Filter[] } = {},
): ListView {
  const client = useContext(ViewsContext)
  const enabled = !!client && fixed.length === 0
  const key = resourceKey(resource.basePath)
  const qc = useQueryClient()
  const meta = useListMeta(enabled ? resource : undefined)
  const mineQ = useQuery({
    queryKey: ['platform-views', key], queryFn: () => client!.list(key), enabled, staleTime: 30_000,
  })
  const [spec, setSpecRaw] = useState<QuerySpec>({ page: 1, pageSize, filters: fixed })
  const [layout, setLayout] = useState<Layout>({ columns: [], hidden: [] })
  const [current, setCurrent] = useState<CurrentView | null>(null)
  const [baseline, setBaseline] = useState<ViewState | null>(null)
  const [opened, setOpened] = useState(!enabled)
  const setSpec = (s: QuerySpec) => setSpecRaw(fixed.length
    ? { ...s, filters: [...fixed, ...(s.filters ?? []).filter((f) => !fixed.some((x) => x.field === f.field))] }
    : s)

  const system = useMemo(() => meta.data?.views ?? [], [meta.data])
  const mine = useMemo(() => mineQ.data ?? [], [mineQ.data])
  const apply = (v: CurrentView, state: ViewState) => {
    setCurrent(v)
    setBaseline(state)
    setSpecRaw(specOf(state))
    setLayout(layoutOf(state))
  }
  const stateOf = (v: CurrentView): ViewState | null => {
    if (v.kind === 'system') {
      const s = system.find((x) => x.key === v.key)
      return s ? fullState(s.state, pageSize) : null
    }
    const m = mine.find((x) => x.id === v.id)
    return m ? fullState(m.state, pageSize) : null
  }

  // Opening: a requested system view, else the user's default, else the
  // list's first system view — once both lists of views have arrived (or
  // failed). Done while rendering (state adjusted from data that arrived), so
  // the list's first request already carries the view.
  const settled = (meta.isSuccess || meta.isError) && (mineQ.isSuccess || mineQ.isError)
  const request = useViewRequest(key)
  if (enabled && settled) {
    const asked = request ? system.find((s) => s.key === request) : undefined
    if (asked) {
      takeViewRequest(key)  // taken once, here; a request for an unknown view stays until one exists
      apply({ kind: 'system', key: asked.key, name: asked.name }, fullState(asked.state, pageSize))
      if (!opened) setOpened(true)
    } else if (!opened) {
      const def = mine.find((m) => m.is_default)
      if (def) apply({ kind: 'mine', id: def.id, name: def.name }, fullState(def.state, pageSize))
      else if (system[0]) apply({ kind: 'system', key: system[0].key, name: system[0].name }, fullState(system[0].state, pageSize))
      setOpened(true)
    }
  }

  const now = viewStateOf(spec, layout)
  const dirty = !!baseline && !sameView(now, baseline)
  const refresh = () => qc.invalidateQueries({ queryKey: ['platform-views', key] })
  const m = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: refresh })
  const run = (fn: () => Promise<unknown>) => m.mutateAsync(fn).then(() => undefined)

  const picker: ViewPickerModel | undefined = enabled ? {
    system, mine, current, dirty, busy: m.isPending, error: m.error,
    stale: current?.kind === 'mine' && !!mine.find((x) => x.id === current.id)?.stale,
    select: (v) => { const s = stateOf(v); if (s) apply(v, s) },
    reset: () => { if (current && baseline) apply(current, baseline) },
    save: async () => {
      if (current?.kind !== 'mine') return
      const row = mine.find((x) => x.id === current.id)
      await run(async () => {
        const saved = await client!.update(current.id, { state: now }, row?.version)
        setBaseline(fullState(saved.state, pageSize))
      })
    },
    saveAs: async (name) => {
      await run(async () => {
        const saved = await client!.create(key, name, now)
        setCurrent({ kind: 'mine', id: saved.id, name: saved.name })
        setBaseline(fullState(saved.state, pageSize))
      })
    },
    rename: async (id, name) => {
      await run(async () => {
        const saved = await client!.update(id, { name })
        if (current?.kind === 'mine' && current.id === id) setCurrent({ ...current, name: saved.name })
      })
    },
    remove: async (id) => {
      await run(() => client!.remove(id))
      if (current?.kind === 'mine' && current.id === id && system[0]) {
        apply({ kind: 'system', key: system[0].key, name: system[0].name }, fullState(system[0].state, pageSize))
      }
    },
    setDefault: (id, on) => run(() => client!.setDefault(id, on)),
  } : undefined

  return {
    spec, setSpec, layout, setLayout, ready: opened, picker,
    tableProps: { spec, onSpecChange: setSpec, layout, onLayoutChange: setLayout, picker },
  }
}
