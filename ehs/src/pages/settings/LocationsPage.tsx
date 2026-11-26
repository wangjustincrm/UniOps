/**
 * The plant tree.
 *
 * `locations` is master data owned by the master-data service, and this screen
 * lives in Safety because the HSE Manager is who keeps it current — data
 * ownership and screen location are deliberately different here. Writes go
 * back through the owning service rather than touching the mirror.
 *
 * Until now this screen was read-only, which meant the tree had never been
 * edited by a person at all: it was seeded by a migration, and every change
 * since has been another migration. An area that gets renamed, a line that
 * gets commissioned, a room that is no longer used — none of it could be
 * recorded, and every incident on a new line went on filing against the area
 * above it.
 *
 * ★ UAT 17: "merge the 'plant area' registry with the 'area responsibility'
 * registry." Who covers each area is shown on its row and changed there. An
 * area carries everything under it: naming somebody for "103 – Dryer" names
 * them for Line 1 and Line 2 too.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Pencil, Plus, QrCode, Trash2, UserPlus, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import QrPanel from '@/components/QrPanel'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, ErrorNote, Field, inputClass, LoadFailedNote, PageHeader, primaryButton, secondaryButton,
} from '@/components/ui'

interface Node {
  id: string; code: string; name: string; level: string; path: string
  depth: number; access_area: string | null; qr_token: string | null
  is_active: boolean; is_selectable: boolean; children: Node[]
}

const LEVEL_LABEL: Record<string, string> = {
  site: 'Site', building: 'Building', area: 'Area', department: 'Department',
  line: 'Line', sub_line: 'Line', room: 'Room', workstation: 'Workstation',
}

interface AreaOwner {
  id: string
  user_id: string
  user_name: string | null
  location_id: string
  location_path: string
}

interface Unattributed { count: number; examples: string[] }

/** Who is down for each area, and changing it — the old Area responsibility
 * page, on the rows it was about. */
interface Owners {
  of: (locationId: string) => AreaOwner[]
  mayManage: boolean
  adding: string | null
  setAdding: (locationId: string | null) => void
}

/** Offered in the order the plant is actually structured, deepest last. */
const LEVELS = ['area', 'department', 'line', 'sub_line', 'room', 'workstation'] as const

/** The level a child of this node would most likely be. */
function childLevel(parent: Node | null): string {
  if (!parent) return 'area'
  const i = LEVELS.indexOf(parent.level as (typeof LEVELS)[number])
  return i >= 0 && i < LEVELS.length - 1 ? LEVELS[i + 1] : 'line'
}

export default function LocationsPage() {
  const { can } = usePermissions()
  const mayEdit = can('mdm.location.write')
  const mayManage = can('ehs.settings.manage')
  const [adding, setAdding] = useState<string | null>(null)
  // The sticker by a door is the one thing here somebody uses without being
  // asked to: scanning it opens the incident form with the area filled in.
  const [showing, setShowing] = useState<Node | null>(null)
  const [editing, setEditing] = useState<Node | null>(null)
  const [addingUnder, setAddingUnder] = useState<Node | null | undefined>(undefined)
  const [showRetired, setShowRetired] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['locations', showRetired],
    queryFn: () => api.get<Node[]>(
      `/api/v1/settings/locations${showRetired ? '?include_inactive=true' : ''}`),
  })

  const owners = useQuery({
    queryKey: ['area-owners'],
    queryFn: () => api.get<AreaOwner[]>('/api/v1/settings/area-owners'),
  })
  // How much work is waiting on an area nobody is down for.
  const orphans = useQuery({
    queryKey: ['unattributed'],
    queryFn: () => api.get<Unattributed>('/api/v1/actions/unattributed'),
  })
  const byArea = new Map<string, AreaOwner[]>()
  for (const r of owners.data ?? []) {
    byArea.set(r.location_id, [...(byArea.get(r.location_id) ?? []), r])
  }
  const inTree = new Set<string>()
  const walk = (nodes?: Node[]) => nodes?.forEach((n) => { inTree.add(n.id); walk(n.children) })
  walk(data)
  const elsewhere = new Map<string, AreaOwner[]>()
  for (const r of owners.data ?? []) {
    if (!inTree.has(r.location_id)) {
      elsewhere.set(r.location_path, [...(elsewhere.get(r.location_path) ?? []), r])
    }
  }
  const ownersOf: Owners = {
    of: (locationId) => byArea.get(locationId) ?? [], mayManage, adding, setAdding,
  }

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>

  return (
    <div className="p-4">
      <PageHeader
        title="Plant areas"
        subtitle="Every incident, inspection and corrective action is filed against one of these, and each area shows who is responsible for it — everything underneath comes with it. Renaming an area updates the tree; records already filed keep the wording they were filed under."
      >
        {mayEdit && (
          <button type="button" className={primaryButton} onClick={() => setAddingUnder(null)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a top-level area
          </button>
        )}
      </PageHeader>

      {showing && (
        <div className="mb-3 rounded-xl border border-neutral-200 bg-white p-3.5">
          <QrPanel token={showing.qr_token} caption={showing.name} sub={showing.path} />
          <button type="button" onClick={() => setShowing(null)}
                  className="mt-2 text-xs text-neutral-500 hover:text-neutral-800">
            Close
          </button>
        </div>
      )}

      {owners.isError && <LoadFailedNote what="Who is responsible for each area" />}
      {(orphans.data?.count ?? 0) > 0 && (
        <div className="mb-3 rounded-lg bg-warning-50 px-3 py-2.5">
          <p className="text-sm font-semibold text-warning-800">
            {orphans.data!.count}{' '}
            {orphans.data!.count === 1 ? 'task belongs' : 'tasks belong'} to a part of
            the plant nobody is responsible for.
          </p>
          <p className="mt-1 text-xs text-warning-700">
            They are on the HSE list in the meantime. Naming somebody for the area
            moves them.
          </p>
          <ul className="mt-1.5 list-inside list-disc text-xs text-warning-700">
            {orphans.data!.examples.map((e) => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      {addingUnder !== undefined && (
        <AreaForm parent={addingUnder} onClose={() => setAddingUnder(undefined)} />
      )}
      {editing && <AreaForm existing={editing} onClose={() => setEditing(null)} />}

      <label className="mb-3 flex items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" checked={showRetired}
               onChange={(e) => setShowRetired(e.target.checked)} />
        Include areas no longer part of the plant
      </label>

      <ul className="rounded-xl border border-neutral-200 bg-white">
        {data?.map((n) => (
          <TreeNode key={n.id} node={n} mayEdit={mayEdit} onShowCode={setShowing}
                    onEdit={setEditing} onAddChild={setAddingUnder} owners={ownersOf} />
        ))}
      </ul>

      {elsewhere.size > 0 && (
        <Card className="mt-4">
          <p className="text-sm font-semibold text-neutral-900">
            Still responsible for areas not shown above
          </p>
          <p className="mb-2 text-xs text-neutral-500">
            These areas are no longer part of the plant, or are hidden by the box above.
          </p>
          <ul className="flex flex-col gap-2">
            {[...elsewhere.entries()].map(([path, people]) => (
              <li key={path}>
                <p className="text-xs font-medium text-neutral-700">{path.split('/').join(' › ')}</p>
                <OwnerChips where={path} owners={people} mayManage={mayManage} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function TreeNode({ node, mayEdit, onShowCode, onEdit, onAddChild, owners }: {
  node: Node; mayEdit: boolean
  onShowCode: (n: Node) => void; onEdit: (n: Node) => void; onAddChild: (n: Node) => void
  owners: Owners
}) {
  const here = owners.of(node.id)
  return (
    <>
      <li
        className={`flex flex-wrap items-center gap-2.5 border-b border-neutral-100 px-3.5 py-2.5 last:border-b-0 ${
          node.is_active ? '' : 'bg-neutral-50'}`}
        style={{ paddingLeft: `${14 + node.depth * 18}px` }}
      >
        <span className={`min-w-0 flex-1 text-sm ${
          node.is_active ? 'text-neutral-800' : 'text-neutral-400 line-through'}`}>
          {node.name}
        </span>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
          {LEVEL_LABEL[node.level] ?? node.level}
        </span>
        {!node.is_selectable && (
          <span title="A heading only — nothing can be filed against it directly"
                className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
            Heading
          </span>
        )}
        {node.access_area && (
          <span className="hidden text-xs text-neutral-400 sm:inline">{node.access_area}</span>
        )}

        {mayEdit && node.is_active && (
          <>
            <button
              type="button"
              onClick={() => onAddChild(node)}
              title={`Add something inside ${node.name}`}
              className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-400 hover:bg-neutral-50 hover:text-neutral-800"
            >
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              Add inside
            </button>
            <button
              type="button"
              onClick={() => onEdit(node)}
              className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-400 hover:bg-neutral-50 hover:text-neutral-800"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              Edit
            </button>
          </>
        )}
        {mayEdit && !node.is_active && <RestoreButton node={node} />}

        {node.qr_token && (
          <button
            type="button"
            onClick={() => onShowCode(node)}
            title="Show the code to print for this area"
            className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-400 hover:bg-neutral-50 hover:text-neutral-800"
          >
            <QrCode className="h-3.5 w-3.5" aria-hidden />
            Code
          </button>
        )}
        {owners.mayManage && node.is_active && (
          <button
            type="button"
            onClick={() => owners.setAdding(owners.adding === node.id ? null : node.id)}
            title={`Make somebody responsible for ${node.name}`}
            className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-400 hover:bg-neutral-50 hover:text-neutral-800"
          >
            <UserPlus className="h-3.5 w-3.5" aria-hidden />
            Responsible
          </button>
        )}
        {here.length > 0 && <OwnerChips where={node.path} owners={here} mayManage={owners.mayManage} />}
      </li>
      {owners.adding === node.id && (
        <li className="border-b border-neutral-100 bg-neutral-50 px-3.5 py-3"
            style={{ paddingLeft: `${14 + node.depth * 18}px` }}>
          <AddOwner node={node} onDone={() => owners.setAdding(null)} />
        </li>
      )}
      {node.children?.map((c) => (
        <TreeNode key={c.id} node={c} mayEdit={mayEdit} onShowCode={onShowCode}
                  onEdit={onEdit} onAddChild={onAddChild} owners={owners} />
      ))}
    </>
  )
}

function useOwnersRefresh() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ['area-owners'] })
    queryClient.invalidateQueries({ queryKey: ['unattributed'] })
    queryClient.invalidateQueries({ queryKey: ['actions', 'mine', 'tasks'] })
  }
}

function OwnerChips({ where, owners, mayManage }: {
  where: string; owners: AreaOwner[]; mayManage: boolean
}) {
  const refresh = useOwnersRefresh()
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/v1/settings/area-owners/${id}`),
    onSuccess: refresh,
  })
  return (
    <ul className="flex w-full flex-wrap items-center gap-1.5" aria-label={`Responsible for ${where}`}>
      {owners.map((r) => (
        <li key={r.id}
            className="flex items-center gap-1.5 rounded-full bg-primary-50 px-2.5 py-1 text-xs text-primary-800">
          {r.user_name ?? 'Somebody who has left'}
          {mayManage && (
            <button type="button"
                    onClick={() => { if (!remove.isPending) remove.mutate(r.id) }}
                    className="flex min-h-[24px] min-w-[24px] items-center justify-center text-primary-400 hover:text-danger-700"
                    aria-label={`Remove ${r.user_name ?? 'this person'} from ${where}`}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </button>
          )}
        </li>
      ))}
      {remove.error && (
        <li className="text-xs text-danger-700">
          {remove.error instanceof ApiError ? remove.error.message : String(remove.error)}
        </li>
      )}
    </ul>
  )
}

function AddOwner({ node, onDone }: { node: Node; onDone: () => void }) {
  const refresh = useOwnersRefresh()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const add = useMutation({
    mutationFn: () => api.post('/api/v1/settings/area-owners', {
      user_id: person.userId, location_id: node.id,
    }),
    onSuccess: () => { refresh(); onDone() },
  })
  return (
    <div className="flex flex-col gap-2">
      <Field label={`Who is responsible for ${node.name}`}
             hint="Everything underneath it comes with it">
        <PersonPicker value={person} onChange={setPerson} />
      </Field>
      {!person.userId && person.name.trim() !== '' && (
        <p className="text-xs text-neutral-500">
          Only people with a UniOps account can be made responsible for an area —
          the work has to reach somebody's list.
        </p>
      )}
      <ErrorNote error={add.error} />
      <div className="flex gap-2">
        <button type="button" className={secondaryButton} onClick={onDone}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!person.userId || add.isPending}
                onClick={() => { if (person.userId && !add.isPending) add.mutate() }}>
          <UserPlus className="h-4 w-4" aria-hidden />
          {add.isPending ? 'Saving…' : 'Make responsible'}
        </button>
      </div>
    </div>
  )
}

function RestoreButton({ node }: { node: Node }) {
  const queryClient = useQueryClient()
  const restore = useMutation({
    mutationFn: () => api.post(`/api/v1/settings/locations/${node.id}/restore`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['locations'] }),
  })
  return (
    <button type="button" disabled={restore.isPending}
            onClick={() => { if (!restore.isPending) restore.mutate() }}
            className="min-h-[32px] rounded-md px-1.5 text-xs font-semibold text-primary-700 hover:bg-primary-50">
      Bring it back
    </button>
  )
}

/**
 * Adding an area, and correcting one.
 *
 * No code field: HSE name areas, and a code typed by hand is one that collides
 * with another or contradicts its parent. The owning service derives it.
 */
function AreaForm({ existing, parent, onClose }: {
  existing?: Node; parent?: Node | null; onClose: () => void
}) {
  const queryClient = useQueryClient()
  const editing = Boolean(existing)
  const [name, setName] = useState(existing?.name ?? '')
  const [level, setLevel] = useState(existing?.level ?? childLevel(parent ?? null))
  const [selectable, setSelectable] = useState(existing?.is_selectable ?? true)
  const [retiring, setRetiring] = useState(false)

  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['locations'] })
    onClose()
  }

  const save = useMutation({
    mutationFn: () => (editing
      ? api.patch(`/api/v1/settings/locations/${existing!.id}`,
                  { name, level, is_selectable: selectable })
      : api.post('/api/v1/settings/locations', {
          name, level, is_selectable: selectable, parent_id: parent?.id ?? null,
        })),
    onSuccess: done,
  })

  const retire = useMutation({
    mutationFn: () => api.post(`/api/v1/settings/locations/${existing!.id}/retire`, {}),
    onSuccess: done,
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">
          {editing ? `Edit ${existing!.name}`
                   : parent ? `Add inside ${parent.name}` : 'Add a top-level area'}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="What it is called">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Line 1" />
        </Field>
        <Field label="Kind">
          <select className={inputClass} value={level} onChange={(e) => setLevel(e.target.value)}>
            {LEVELS.map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
          </select>
        </Field>
      </div>

      <label className="mt-3 flex max-w-[68ch] items-start gap-2 text-xs text-neutral-600">
        <input type="checkbox" className="mt-0.5" checked={selectable}
               onChange={(e) => setSelectable(e.target.checked)} />
        <span>
          Something can be filed against this area itself. Leave it on for an
          area with lines underneath: somebody who does not know which line it
          happened on should be able to say so, and a guessed line is worse
          than no line.
        </span>
      </label>

      <ErrorNote error={save.error ?? retire.error} />

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        {editing && !retiring && (
          <button type="button"
                  className="mr-auto text-xs font-semibold text-danger-700 hover:underline"
                  onClick={() => setRetiring(true)}>
            No longer part of the plant
          </button>
        )}
        {editing && retiring && (
          <span className="mr-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-neutral-600">
              Take {existing!.name} and everything inside it off the list?
              Past records keep it.
            </span>
            <button type="button" className={secondaryButton}
                    onClick={() => setRetiring(false)}>Cancel</button>
            <button type="button" className={primaryButton} disabled={retire.isPending}
                    onClick={() => { if (!retire.isPending) retire.mutate() }}>
              {retire.isPending ? 'Removing…' : 'Take it off'}
            </button>
          </span>
        )}
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!name.trim() || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : editing ? 'Save' : 'Add'}
        </button>
      </div>
    </Card>
  )
}
