import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { INPUT, PRIMARY_BTN, SECONDARY_BTN, useStaffPerm } from '@/pages/mdm/staff/shared'
import { Combobox, type ComboOption } from './Combobox'

type Relation = 'child' | 'assistant'
type Drop = 'before' | 'after' | 'inside'
interface Box { id: string; parent_id: string | null; relation: Relation; tone: string | null }
interface Unit extends Box {
  name: string; department_id: string | null; department_code: string | null
  head_employee_id: string | null; head_name: string | null; employees: number
}
interface Person extends Box {
  employee_id: string | null; employee_number: string | null; name: string; title: string | null
  job_title: string | null; custom_title: string | null; department: string | null
  adp_department: string | null; source: string; review: boolean
}

const STUB = 18
const LINE = '#cbd5d5'
const LINES = `
.org-tree ul { display: flex; justify-content: center; position: relative; padding-top: ${STUB}px; margin: 0; }
.org-tree > ul { padding-top: 0; }
.org-tree li { list-style: none; position: relative; padding: ${STUB}px 6px 0; display: flex; flex-direction: column; align-items: center; }
.org-tree > ul > li { padding-top: 0; }
.org-tree li::before, .org-tree li::after { content: ''; position: absolute; top: 0; right: 50%; width: 50%; height: ${STUB}px; border-top: 1px solid ${LINE}; }
.org-tree li::after { right: auto; left: 50%; border-left: 1px solid ${LINE}; }
.org-tree > ul > li::before, .org-tree > ul > li::after { display: none; }
.org-tree li:only-child::before { display: none; }
.org-tree li:only-child::after { left: 50%; width: 0; border-top: 0; }
.org-tree li:first-child:not(:only-child)::before, .org-tree li:last-child:not(:only-child)::after { border: 0 none; }
.org-tree li:last-child:not(:only-child)::before { border-right: 1px solid ${LINE}; }
.org-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; height: ${STUB}px; border-left: 1px solid ${LINE}; }
/* A long list of peers hangs off one trunk, each on its own branch. */
.org-tree ul.hang { flex-direction: column; align-self: stretch; align-items: flex-start; gap: 6px; padding: 8px 0 0 22px; }
.org-tree ul.hang::before { top: 0; left: 10px; height: calc(100% - 28px); }
.org-tree ul.hang > li { padding: 0; }
.org-tree ul.hang > li::after { display: none; }
.org-tree ul.hang > li::before { display: block; top: 50%; left: -12px; right: auto; width: 12px; height: 0; border: 0; border-top: 1px solid ${LINE}; }
.org-tree ul.hang .org-card { width: 154px; }
/* Beside the line: reports to the box above, sits above the boxes below. */
.org-assist { display: grid; grid-template-columns: 196px 196px; row-gap: 6px; position: relative; padding: 10px 0; }
.org-assist::before { content: ''; position: absolute; top: 0; bottom: 0; left: 50%; border-left: 1px solid ${LINE}; }
.org-assist > div { position: relative; }
.org-assist > .right { grid-column: 2; padding-left: 20px; }
.org-assist > .left { grid-column: 1; padding-right: 20px; }
.org-assist > .right::before, .org-assist > .left::before { content: ''; position: absolute; top: 28px; width: 20px; border-top: 1px dashed ${LINE}; }
.org-assist > .right::before { left: 0; }
.org-assist > .left::before { right: 0; }
`
const MAX_SIDE_BY_SIDE = 4
const TONES: Record<string, string> = {
  teal: 'bg-[#E4F3EE] border-[#9CCFBD]',
  violet: 'bg-[#EEEBFB] border-[#BDB5EC]',
  outside: 'bg-white border-dashed border-neutral-300',
}
const CARD = 'org-card block w-44 rounded-lg border bg-white px-2.5 py-2 text-left shadow-sm hover:border-[#085E5E] transition-colors'

function childrenOf<T extends Box>(nodes: T[]) {
  const ids = new Set(nodes.map((n) => n.id))
  const kids = new Map<string | null, T[]>()
  for (const n of nodes) {
    const key = n.parent_id && ids.has(n.parent_id) ? n.parent_id : null
    kids.set(key, [...(kids.get(key) ?? []), n])
  }
  return kids
}

/** Top-down chart. A card dragged onto another moves under it. */
function Tree<T extends Box>({ nodes, open, onToggle, card, onDropOn }: {
  nodes: T[]; open: Set<string>; onToggle: (id: string) => void
  card: (node: T) => ReactNode; onDropOn?: (dragged: string, target: string, where: Drop) => void
}) {
  const kids = useMemo(() => childrenOf(nodes), [nodes])
  const [over, setOver] = useState<{ id: string; where: Drop } | null>(null)

  // The edges of a card mean next to it, the middle means under it. Peers in a hanging list are one above the other.
  const zone = (e: DragEvent<HTMLDivElement>, stacked: boolean): Drop => {
    const r = e.currentTarget.getBoundingClientRect()
    const at = stacked ? (e.clientY - r.top) / r.height : (e.clientX - r.left) / r.width
    return at < 0.3 ? 'before' : at > 0.7 ? 'after' : 'inside'
  }
  const face = (node: T, stacked = false) => (
    <div draggable={!!onDropOn} data-node={node.id}
      onDragStart={(e) => e.dataTransfer.setData('text/plain', node.id)}
      onDragOver={(e) => { if (onDropOn) { e.preventDefault(); setOver({ id: node.id, where: zone(e, stacked) }) } }}
      onDragLeave={() => setOver(null)}
      onDrop={(e) => {
        e.preventDefault(); setOver(null)
        const dragged = e.dataTransfer.getData('text/plain')
        if (dragged && dragged !== node.id) onDropOn?.(dragged, node.id, zone(e, stacked))
      }}
      className={cn('relative rounded-lg', over?.id === node.id && over.where === 'inside' && 'ring-2 ring-[#085E5E]')}>
      {card(node)}
      {over?.id === node.id && over.where !== 'inside' && (
        <span className={cn('pointer-events-none absolute rounded bg-[#085E5E]', stacked
          ? cn('inset-x-0 h-1', over.where === 'before' ? '-top-1' : '-bottom-1')
          : cn('inset-y-0 w-1', over.where === 'before' ? '-left-1.5' : '-right-1.5'))} />
      )}
    </div>
  )

  const under = (node: T) => {
    const all = kids.get(node.id) ?? []
    const assistants = all.filter((k) => k.relation === 'assistant')
    const reports = all.filter((k) => k.relation !== 'assistant')
    const hang = reports.length > MAX_SIDE_BY_SIDE && reports.every((r) => !kids.get(r.id)?.length)
    const shown = open.has(node.id)
    return (
      <>
        {all.length > 0 && (
          <button aria-label={shown ? 'Collapse' : 'Expand'} onClick={() => onToggle(node.id)}
            className="z-[1] -mt-px flex items-center gap-0.5 rounded-b border border-t-0 border-neutral-200 bg-white px-1.5 text-[11px] text-neutral-500 hover:text-[#085E5E]">
            {shown ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{all.length}
          </button>
        )}
        {shown && assistants.length > 0 && (
          <div className="org-assist">
            {assistants.map((a, i) => (
              <div key={a.id} className={i % 2 ? 'left' : 'right'}>
                {face(a)}
                <div className="flex flex-col items-center">{under(a)}</div>
              </div>
            ))}
          </div>
        )}
        {shown && reports.length > 0 && <ul className={cn(hang && 'hang')}>{reports.map((r) => branch(r, hang))}</ul>}
      </>
    )
  }
  const branch = (node: T, stacked = false): ReactNode => <li key={node.id}>{face(node, stacked)}{under(node)}</li>
  return <div className="org-tree inline-block min-w-full p-4"><ul>{(kids.get(null) ?? []).map((n) => branch(n))}</ul></div>
}

function BoxStyle({ box, disabled, onSet }: { box: Box; disabled: boolean; onSet: (patch: object) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <label className="block text-xs text-neutral-500">Position
        <select className={`${INPUT} mt-1 w-full`} disabled={disabled} value={box.relation}
          onChange={(e) => onSet({ relation: e.target.value })}>
          <option value="child">Below</option>
          <option value="assistant">On the line</option>
        </select>
      </label>
      <label className="block text-xs text-neutral-500">Style
        <select className={`${INPUT} mt-1 w-full`} disabled={disabled} value={box.tone ?? ''}
          onChange={(e) => onSet({ tone: e.target.value || null })}>
          <option value="">Plain</option>
          <option value="teal">Green</option>
          <option value="violet">Purple</option>
          <option value="outside">Dashed</option>
        </select>
      </label>
    </div>
  )
}

type Where = 'below' | 'beside' | 'line'
const WHERE: { key: Where; label: string; title: string }[] = [
  { key: 'below', label: 'Below', title: 'A box under this one' },
  { key: 'beside', label: 'Beside', title: 'A box at the same level as this one' },
  { key: 'line', label: 'On the line', title: 'A box beside the line under this one: under this box, above the ones below it' },
]

function AddBox({ withTitle, disabled, onAdd }: {
  withTitle: boolean; disabled: boolean; onAdd: (where: Where, name: string, title: string) => Promise<unknown>
}) {
  const [where, setWhere] = useState<Where | null>(null)
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  return (
    <div className="border-t border-neutral-100 pt-3">
      <p className="mb-1.5 text-xs text-neutral-500">Add a box</p>
      <div className="flex gap-1">
        {WHERE.map((w) => (
          <button key={w.key} title={w.title} onClick={() => setWhere(where === w.key ? null : w.key)}
            className={cn('flex-1 rounded-md border px-1.5 py-1.5 text-xs font-medium',
              where === w.key ? 'border-[#085E5E] bg-[#E9F3F3] text-[#085E5E]' : 'border-neutral-200 text-neutral-600 hover:bg-neutral-50')}>
            {w.label}
          </button>
        ))}
      </div>
      {where && (
        <div className="mt-2 space-y-2">
          <input autoFocus className={`${INPUT} w-full`} placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
          {withTitle && <input className={`${INPUT} w-full`} placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />}
          <button className={PRIMARY_BTN} disabled={disabled || !name.trim()} onClick={async () => {
            await onAdd(where, name, title); setName(''); setTitle(''); setWhere(null)
          }}>Add</button>
        </div>
      )}
    </div>
  )
}

/** Saved on leaving the field; keyed by the box so it reloads when another card is picked. */
function SavedInput({ label, value, placeholder, disabled, onSave }: {
  label: string; value: string; placeholder?: string; disabled: boolean; onSave: (value: string) => void
}) {
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  return (
    <label className="block text-xs text-neutral-500">{label}
      <input className={`${INPUT} mt-1 w-full`} value={text} placeholder={placeholder} disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => { if (text.trim() !== value) onSave(text.trim()) }} />
    </label>
  )
}

export default function OrgChart() {
  const qc = useQueryClient()
  const { can, loading } = useStaffPerm()
  const [view, setView] = useState<'units' | 'people'>('units')
  const [picked, setPicked] = useState<string | null>(null)
  const [openUnits, setOpenUnits] = useState<Set<string> | null>(null)
  const [openPeople, setOpenPeople] = useState<Set<string> | null>(null)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [zoom, setZoom] = useState(1)
  const scroller = useRef<HTMLDivElement>(null)

  const allowed = can('employee.view')
  const { data: unitData } = useQuery<{ items: Unit[] }>({
    queryKey: ['org-units'], queryFn: () => mdmApi.get('/org/units'), enabled: allowed,
  })
  const { data: peopleData } = useQuery<{ items: Person[] }>({
    queryKey: ['org-nodes'], queryFn: () => mdmApi.get('/org/nodes'), enabled: allowed,
  })
  const units = useMemo(() => unitData?.items ?? [], [unitData])
  const people = useMemo(() => peopleData?.items ?? [], [peopleData])
  const byId = useMemo(() => new Map(people.map((p) => [p.id, p])), [people])
  const peopleOptions = useMemo<ComboOption[]>(() => people.map((p) => ({
    value: p.id, label: p.name, hint: p.title, search: `${p.employee_number ?? ''} ${p.adp_department ?? ''}`,
  })), [people])
  const employeeOptions = useMemo<ComboOption[]>(() => people.filter((p) => p.employee_id).map((p) => ({
    value: p.employee_id as string, label: p.name, hint: p.title, search: `${p.employee_number ?? ''} ${p.adp_department ?? ''}`,
  })), [people])

  const fit = (smallest = 0.4) => {
    const el = scroller.current
    if (!el) return
    const natural = el.scrollWidth / zoom
    setZoom(Math.max(smallest, Math.min(1, Math.floor((el.clientWidth / natural) * 20) / 20)))
  }
  // The chart is usually wider than the page: open it with the top of the chart in view.
  const ready = view === 'units' ? units.length > 0 : people.length > 0
  useEffect(() => {
    if (ready) fit(0.7)   // readable first; the % button shows the whole chart
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, ready])
  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2
  }, [zoom, view, ready])

  if (loading) return null
  if (!allowed) return <Navigate to="/" replace />
  const editable = can('employee.edit')

  const run = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await action()
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['org-units'] }),
        qc.invalidateQueries({ queryKey: ['org-nodes'] }),
      ])
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const setUnit = (id: string, patch: object) => run(() => mdmApi.patch(`/org/units/${id}`, patch))
  const setNode = (id: string, patch: object) => run(() => mdmApi.patch(`/org/nodes/${id}`, patch))
  const drop = (path: string) => (id: string, target: string, where: Drop) =>
    run(() => mdmApi.post(`/org/${path}/${id}/move`, { target_id: target, where }))
  const generate = () => run(async () => {
    const r = await mdmApi.post<{ people_set: number; to_review: number }>('/org/generate')
    setNotice(`${r.people_set} reporting lines worked out, ${r.to_review} to check. Lines set by hand were kept.`)
  })
  const addBox = (path: string, base: Box, where: Where, body: object) => run(async () => {
    const r = await mdmApi.post<{ id: string }>(path, {
      ...body,
      parent_id: where === 'beside' ? base.parent_id : base.id,
      relation: where === 'line' ? 'assistant' : where === 'beside' ? base.relation : 'child',
    })
    const open = (set: Set<string>) => new Set(set).add(base.id)
    if (view === 'units') setOpenUnits(open(unitsOpen)); else setOpenPeople(open(peopleOpen))
    setPicked(r.id)
  })

  const hasChart = units.length > 0 || people.length > 0
  const unitsOpen = openUnits ?? new Set(units.map((u) => u.id))
  const defaultPeople = new Set(people.filter((p) => !p.parent_id || !byId.get(p.parent_id)?.parent_id).map((p) => p.id))
  const peopleOpen = openPeople ?? defaultPeople
  const toggle = (set: Set<string>, id: string) => {
    const next = new Set(set)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  }
  const reveal = (id: string) => {
    const next = new Set(peopleOpen)
    for (let p = byId.get(id)?.parent_id; p; p = byId.get(p)?.parent_id) next.add(p)
    setOpenPeople(next); setPicked(id)
    window.setTimeout(() => document.querySelector(`[data-node="${id}"]`)
      ?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }), 50)
  }

  const term = search.trim().toLowerCase()
  const found = term ? people.filter((p) => p.name.toLowerCase().includes(term) || (p.employee_number ?? '').includes(term)).slice(0, 8) : []
  const toCheck = people.filter((p) => p.review)
  const unit = view === 'units' ? units.find((u) => u.id === picked) : undefined
  const person = view === 'people' ? byId.get(picked ?? '') : undefined

  return (
    <PortalPageLayout activeKey="portal:/mdm/org-chart">
      <style>{LINES}</style>
      <div className="p-6">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h1 className="mr-2 text-lg font-semibold text-neutral-800">Org Chart</h1>
          <div className="flex rounded-lg border border-neutral-200 bg-white p-0.5">
            {(['units', 'people'] as const).map((v) => (
              <button key={v} onClick={() => { setView(v); setPicked(null) }}
                className={cn('rounded-md px-3 py-1.5 text-sm font-medium', view === v ? 'bg-[#085E5E] text-white' : 'text-neutral-600')}>
                {v === 'units' ? 'Departments' : 'Reporting Lines'}
              </button>
            ))}
          </div>
          {view === 'people' && (
            <div className="relative">
              <input className={`${INPUT} w-60`} placeholder="Find a person" value={search} onChange={(e) => setSearch(e.target.value)} />
              {found.length > 0 && (
                <div className="absolute z-20 mt-1 w-72 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                  {found.map((p) => (
                    <button key={p.id} className="block w-full px-3 py-1.5 text-left text-sm hover:bg-neutral-50"
                      onClick={() => { setSearch(''); reveal(p.id) }}>
                      {p.name} <span className="text-xs text-neutral-400">{p.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="ml-auto flex gap-2">
            <div className="flex items-center rounded-lg border border-neutral-200 bg-white text-sm text-neutral-600">
              <button aria-label="Zoom out" className="px-2.5 py-2 hover:text-[#085E5E]" onClick={() => setZoom((z) => Math.max(0.4, z - 0.1))}>−</button>
              <button className="w-12 py-2 hover:text-[#085E5E]" title="Fit to the page" onClick={() => fit()}>{Math.round(zoom * 100)}%</button>
              <button aria-label="Zoom in" className="px-2.5 py-2 hover:text-[#085E5E]" onClick={() => setZoom((z) => Math.min(1.2, z + 0.1))}>+</button>
            </div>
            {view === 'people' && (
              <>
                <button className={SECONDARY_BTN} onClick={() => setOpenPeople(new Set(people.map((p) => p.id)))}>Expand all</button>
                <button className={SECONDARY_BTN} onClick={() => setOpenPeople(defaultPeople)}>Collapse</button>
              </>
            )}
            {editable && (
              <button className={hasChart ? SECONDARY_BTN : PRIMARY_BTN} disabled={busy} onClick={generate}
                title="Works out what is missing from job titles, ADP departments and approval routing. Anything set by hand stays.">
                <RefreshCw className="h-4 w-4" />{busy ? 'Working…' : hasChart ? 'Regenerate' : 'Generate first version'}
              </button>
            )}
          </div>
        </div>
        <p className="mb-3 text-sm text-neutral-500">
          The company as it really is. Approvals do not use this chart; changing it changes no approver.
          {editable && ' Pick a card to edit it or add a box. Drag a card to the middle of another to put it below, or to its edge to put it beside.'}
        </p>
        {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
        {notice && <p className="mb-3 text-sm text-[#085E5E]">{notice}</p>}

        {!hasChart ? (
          <p className="rounded-xl border border-neutral-200 bg-white py-12 text-center text-sm text-neutral-400">
            No chart yet.{editable ? ' Generate the first version, then adjust it.' : ''}
          </p>
        ) : (
          <div className="flex items-start gap-4">
            <div ref={scroller} className="min-w-0 flex-1 overflow-auto rounded-xl border border-neutral-200 bg-neutral-50" style={{ maxHeight: 'calc(100vh - 230px)' }}>
              <div style={{ zoom }}>
                {view === 'units' ? (
                  <Tree nodes={units} open={unitsOpen} onToggle={(id) => setOpenUnits(toggle(unitsOpen, id))}
                    onDropOn={editable ? drop('units') : undefined}
                    card={(u) => (
                      <button onClick={() => setPicked(u.id)} className={cn(CARD, u.tone ? TONES[u.tone] : !u.department_id ? 'bg-[#E9F3F3] border-neutral-200' : 'border-neutral-200', picked === u.id && '!border-[#085E5E]')}>
                        <span className="block truncate text-sm font-medium text-neutral-800">{u.name}</span>
                        <span className="block truncate text-xs text-neutral-500">{u.head_name ?? 'No head'}</span>
                        {u.department_id && <span className="block text-xs text-neutral-400">{u.department_code} · {u.employees} people</span>}
                      </button>
                    )} />
                ) : (
                  <Tree nodes={people} open={peopleOpen} onToggle={(id) => setOpenPeople(toggle(peopleOpen, id))}
                    onDropOn={editable ? drop('nodes') : undefined}
                    card={(p) => (
                      <button onClick={() => setPicked(p.id)} className={cn(CARD, p.tone ? TONES[p.tone] : p.review ? 'border-amber-300' : 'border-neutral-200', picked === p.id && '!border-[#085E5E]')}>
                        <span className="block truncate text-sm font-medium text-neutral-800">{p.name}</span>
                        <span className="block truncate text-xs text-neutral-500">{p.title ?? '—'}</span>
                        {p.employee_id && <span className="block truncate text-xs text-neutral-400">{p.adp_department ?? '—'}</span>}
                      </button>
                    )} />
                )}
              </div>
            </div>

            <aside className="w-72 shrink-0 space-y-3">
              {view === 'people' && toCheck.length > 0 && !person && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                  <p className="mb-1 text-sm font-medium text-amber-800">To check ({toCheck.length})</p>
                  <p className="mb-2 text-xs text-amber-700">Several people could be the manager; the first was picked.</p>
                  <div className="max-h-96 space-y-0.5 overflow-auto">
                    {toCheck.map((p) => (
                      <button key={p.id} className="block w-full truncate text-left text-sm text-amber-900 hover:underline" onClick={() => reveal(p.id)}>{p.name}</button>
                    ))}
                  </div>
                </div>
              )}

              {unit && (
                <div key={unit.id} className="space-y-3 rounded-xl border border-neutral-200 bg-white p-4">
                  {editable && !unit.department_id
                    ? <SavedInput label="Name" value={unit.name} disabled={busy} onSave={(name) => name && setUnit(unit.id, { name })} />
                    : <p className="text-sm font-semibold text-neutral-800">{unit.name}</p>}
                  {editable ? (
                    <>
                      <label className="block text-xs text-neutral-500">Head
                        <Combobox label="Head" disabled={busy} value={unit.head_employee_id ?? ''} placeholder="Search name, number or title…"
                          options={[{ value: '', label: 'No head' }, ...employeeOptions]}
                          onChange={(v) => setUnit(unit.id, { head_employee_id: v || null })} />
                      </label>
                      <label className="block text-xs text-neutral-500">Sits under
                        <Combobox label="Sits under" disabled={busy} value={unit.parent_id ?? ''}
                          options={[{ value: '', label: 'Top of the chart' },
                            ...units.filter((u) => u.id !== unit.id).map((u) => ({ value: u.id, label: u.name }))]}
                          onChange={(v) => setUnit(unit.id, { parent_id: v || null })} />
                      </label>
                      <BoxStyle box={unit} disabled={busy} onSet={(patch) => setUnit(unit.id, patch)} />
                      <AddBox withTitle={false} disabled={busy}
                        onAdd={(where, name) => addBox('/org/units', unit, where, { name })} />
                      {!unit.department_id && (
                        <button className="text-sm text-red-600 hover:underline" disabled={busy} onClick={() => run(async () => {
                          await mdmApi.delete(`/org/units/${unit.id}`); setPicked(null)
                        })}>Remove this box</button>
                      )}
                    </>
                  ) : <p className="text-sm text-neutral-600">{unit.head_name ?? 'No head'}</p>}
                </div>
              )}

              {person && (
                <div key={person.id} className="space-y-3 rounded-xl border border-neutral-200 bg-white p-4">
                  {person.employee_id ? (
                    <div>
                      <Link to={`/mdm/staff/${person.employee_id}`} className="text-sm font-semibold text-[#085E5E] hover:underline">{person.name}</Link>
                      <p className="text-xs text-neutral-400">{person.department ?? person.adp_department ?? '—'}</p>
                    </div>
                  ) : editable
                    ? <SavedInput label="Name" value={person.name} disabled={busy} onSave={(name) => name && setNode(person.id, { name })} />
                    : <p className="text-sm font-semibold text-neutral-800">{person.name}</p>}
                  {editable ? (
                    <>
                      <SavedInput label="Title on the chart" value={person.custom_title ?? ''} placeholder={person.job_title ?? ''} disabled={busy}
                        onSave={(title) => setNode(person.id, { title })} />
                      <label className="block text-xs text-neutral-500">Reports to
                        <Combobox label="Reports to" disabled={busy} value={person.parent_id ?? ''} placeholder="Search name, number or title…"
                          options={[{ value: '', label: 'No one (top of the chart)' }, ...peopleOptions.filter((o) => o.value !== person.id)]}
                          onChange={(v) => setNode(person.id, { parent_id: v || null })} />
                      </label>
                      <BoxStyle box={person} disabled={busy} onSet={(patch) => setNode(person.id, patch)} />
                      {person.source !== 'manual' && (
                        <button className={PRIMARY_BTN} disabled={busy} onClick={() => setNode(person.id, { parent_id: person.parent_id })}>Confirm this line</button>
                      )}
                      <AddBox withTitle disabled={busy}
                        onAdd={(where, name, title) => addBox('/org/nodes', person, where, { name, title })} />
                      {!person.employee_id && (
                        <button className="text-sm text-red-600 hover:underline" disabled={busy} onClick={() => run(async () => {
                          await mdmApi.delete(`/org/nodes/${person.id}`); setPicked(null)
                        })}>Remove this box</button>
                      )}
                    </>
                  ) : <p className="text-sm text-neutral-600">{person.title ?? '—'} · reports to {byId.get(person.parent_id ?? '')?.name ?? 'no one'}</p>}
                  {person.employee_id && <p className="text-xs text-neutral-400">{person.source === 'manual' ? 'Set by a person' : 'Worked out from the data'}</p>}
                </div>
              )}

              {!unit && !person && (view === 'units' || toCheck.length === 0) && (
                <p className="rounded-xl border border-dashed border-neutral-200 p-4 text-sm text-neutral-400">Pick a card to adjust it or add a box.</p>
              )}
            </aside>
          </div>
        )}
      </div>
    </PortalPageLayout>
  )
}
