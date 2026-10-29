/**
 * The lists HSE maintains.
 *
 * Retiring an entry is the only form of removal there is, and the screen says
 * so rather than offering a delete that quietly means something else.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Lock, Plus, X } from 'lucide-react'
import { api, apiBlob, ApiError } from '@/lib/api'
import { PageHeader } from '@/components/ui'

interface Vocabulary {
  code: string; name: string; description: string | null
  is_hierarchical: boolean; is_system_locked: boolean
  item_count: number; active_item_count: number
}
interface Item {
  id: string; code: string; label: string; sort_order: number
  is_active: boolean; parent_id: string | null; path: string | null; trail?: string | null
  attrs?: {
    pictogram_file_id?: string; pictogram_filename?: string; severity?: Record<string, string>
    body_region?: string
  }
  /** Body parts: where the dashboard's figure draws it now (server-worked). */
  body_region?: string | null
}

/**
 * The one list whose entries carry artwork.
 *
 * GHS pictograms are regulated: a red diamond with a specific symbol, issued by
 * Health Canada for WHMIS 2015. They are not drawn by this software and should
 * not be — an approximation of a regulated symbol looks right, so nobody checks
 * it. HSE uploads the official file once per class and every label claiming
 * that class prints it; until then, labels claiming it cannot be approved.
 */
const PICTOGRAM_VOCABULARY = 'ghs_hazard_class' 

/** The server's limit (crud/vocabulary.py MAX_DEPTH). */
const MAX_DEPTH = 4

/** Entries in tree order, each with its depth — sorted within a parent,
 *  never across the list. An entry whose parent is not in the list (hidden
 *  as retired) sits at the top rather than disappearing. */
function asTree(items: Item[]) {
  const ids = new Set(items.map((i) => i.id))
  const kids = new Map<string, Item[]>()
  for (const i of items) {
    const key = i.parent_id && ids.has(i.parent_id) ? i.parent_id : ''
    kids.set(key, [...(kids.get(key) ?? []), i])
  }
  for (const list of kids.values()) {
    list.sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label))
  }
  const ordered: { item: Item; depth: number }[] = []
  const walk = (key: string, depth: number) => {
    for (const i of kids.get(key) ?? []) {
      ordered.push({ item: i, depth })
      walk(i.id, depth + 1)
    }
  }
  walk('', 0)
  const trail = new Map(items.map((i) => [i.id, i.trail ?? i.label]))
  return { ordered, kids, trail }
}

/**
 * Where the next entry goes, and what that does to an entry already in use.
 *
 * ★ Review (uniops-8b): an entry with something inside it is a heading and
 * can no longer be chosen. Records already on it keep it, but HSE should know
 * they are about to change what the pickers offer before they do it.
 */
function UnderNote({ code, item, trail, hasChildren, onClear }: {
  code: string; item: Item; trail: string; hasChildren: boolean; onClear: () => void
}) {
  const usage = useQuery({
    queryKey: ['vocabulary-usage', code, item.id],
    queryFn: () => api.get<{ total: number; by: { what: string; count: number }[] }>(
      `/api/v1/settings/vocabularies/${code}/items/${item.id}/usage`),
    enabled: !hasChildren,
    // Three silent retries left "Checking…" up for most of half a minute.
    retry: 1,
  })
  return (
    <div className="mb-2 rounded-lg border border-primary-200 bg-primary-50/50 px-3 py-2 text-xs text-neutral-700">
      <p className="flex items-center gap-2">
        <span className="min-w-0 flex-1">Adding inside <span className="font-semibold">{trail}</span></span>
        <button type="button" onClick={onClear}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-neutral-500 hover:text-neutral-800">
          <X className="h-3.5 w-3.5" aria-hidden />
          Add at the top instead
        </button>
      </p>
      {!hasChildren && (
        <p className="mt-1 text-neutral-600">
          {item.label} becomes a heading: forms will offer what is inside it, not {item.label} itself.
          {/* ★ Loading or failed is not "nobody uses it" (uniops-8b). */}
          {usage.isPending && (
            <span className="mt-1 block text-neutral-500">Checking which records use it…</span>
          )}
          {usage.isError && (
            <span className="mt-1 block font-medium text-danger-700">
              Could not check how many records use it. Adding still works; they keep it either way.
            </span>
          )}
          {usage.data && usage.data.total === 0 && (
            <span className="mt-1 block text-neutral-500">No record names it yet.</span>
          )}
          {usage.data && usage.data.total > 0 && (
            <span className="mt-1 block font-medium text-warning-800">
              {usage.data.total} {usage.data.total === 1 ? 'record names' : 'records name'} {item.label} (
              {usage.data.by.map((u) => `${u.what}: ${u.count}`).join('; ')}). They keep it; it just
              cannot be chosen again.
            </span>
          )}
        </p>
      )}
    </div>
  )
}

export default function VocabulariesPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [showRetired, setShowRetired] = useState(false)
  // Wording changes. Retiring and re-adding was the only way to fix a typo,
  // which loses the entry's history and every record that pointed at it.
  const [renaming, setRenaming] = useState<string | null>(null)
  const [draft, setDraft] = useState({ code: '', label: '' })

  const vocabs = useQuery({
    queryKey: ['vocabularies'],
    queryFn: () => api.get<Vocabulary[]>('/api/v1/settings/vocabularies'),
  })
  const active = selected ?? vocabs.data?.[0]?.code ?? null
  const current = vocabs.data?.find((v) => v.code === active)

  const items = useQuery({
    queryKey: ['vocabularies', active, showRetired],
    queryFn: () => api.get<Item[]>(
      `/api/v1/settings/vocabularies/${active}/items?include_inactive=${showRetired}`),
    enabled: Boolean(active),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['vocabularies'] })
    // The pickers' own copy, so a new entry is offered without a reload.
    queryClient.invalidateQueries({ queryKey: ['vocabulary'] })
  }
  // Under which entry the next one goes; null is the top.
  const [under, setUnder] = useState<Item | null>(null)
  const add = useMutation({
    mutationFn: () => api.post(`/api/v1/settings/vocabularies/${active}/items`, {
      label: draft.label.trim(),
      // Left out, the server derives it from the wording.
      ...(draft.code.trim() ? { code: draft.code.trim() } : {}),
      parent_id: under?.id ?? null,
    }),
    onSuccess: () => { setDraft({ code: '', label: '' }); invalidate() },
  })
  const tree = current?.is_hierarchical ? asTree(items.data ?? []) : null
  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch(`/api/v1/settings/vocabularies/${active}/items/${id}`, { is_active }),
    onSuccess: invalidate,
  })

  return (
    <div className="p-4">
      <PageHeader title="Lists" subtitle="The choices that appear on forms. Entries are retired, never deleted — a record that used one has to keep making sense." />

      <div className="mb-3 flex flex-wrap gap-1.5">
        {vocabs.data?.map((v) => (
          <button
            key={v.code}
            type="button"
            onClick={() => { setSelected(v.code); setUnder(null) }}
            className={`min-h-[36px] rounded-lg border px-3 text-xs font-medium ${
              v.code === active
                ? 'border-primary-400 bg-primary-50 text-primary-700'
                : 'border-neutral-300 bg-white text-neutral-600'
            }`}
          >
            {v.name}
            <span className="ml-1.5 font-mono text-neutral-400">{v.active_item_count}</span>
          </button>
        ))}
      </div>

      {current?.is_system_locked && (
        <p className="mb-3 flex items-start gap-2 rounded-lg bg-neutral-100 px-3 py-2 text-xs text-neutral-600">
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          This list is fixed — its entries drive statutory reporting or follow an
          external standard. The wording can be edited; the set cannot.
        </p>
      )}

      {current && !current.is_system_locked && under && (
        <UnderNote code={active!} item={under} trail={tree?.trail.get(under.id) ?? under.label}
                   hasChildren={Boolean(tree?.kids.get(under.id)?.length)}
                   onClear={() => setUnder(null)} />
      )}
      {current?.is_hierarchical && !current.is_system_locked && !under && (
        <p className="mb-2 text-xs text-neutral-500">
          Adding at the top of the list. To add an entry inside another one, press
          “Add inside” on that entry.
        </p>
      )}
      {current && !current.is_system_locked && (
        <form
          className="mb-3 flex flex-wrap gap-2"
          onSubmit={(e) => { e.preventDefault(); if (!add.isPending) add.mutate() }}
        >
          <input
            className="min-h-[44px] w-28 rounded-lg border border-neutral-300 px-2.5 text-sm"
            placeholder="Code (optional)" value={draft.code} aria-label="Code (optional)"
            onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })}
          />
          <input
            className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
            placeholder={under ? `Inside ${under.label}: what it says on the form` : 'What it says on the form'}
            aria-label="What it says on the form" value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
          <button
            type="submit"
            disabled={add.isPending || !draft.label.trim()}
            className="flex min-h-[44px] items-center gap-1.5 rounded-lg bg-primary-600 px-3.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            <Plus className="h-4 w-4" aria-hidden />
            {under ? `Add inside ${under.label}` : 'Add'}
          </button>
        </form>
      )}
      {add.isError && (
        <p className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {(add.error as ApiError).message}
        </p>
      )}

      {toggle.isError && (
        <p role="alert" className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {(toggle.error as ApiError).message}
        </p>
      )}
      <label className="mb-2 flex min-h-[36px] items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" className="h-3.5 w-3.5" checked={showRetired}
               onChange={(e) => setShowRetired(e.target.checked)} />
        Show retired entries
      </label>

      <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 bg-white">
        {(tree ? tree.ordered : (items.data ?? []).map((item) => ({ item, depth: 0 }))).map(({ item: i, depth }) => (
          <li key={i.id} className={`flex flex-wrap items-center gap-3 py-2.5 pr-3.5 ${i.is_active ? '' : 'opacity-50'}`}
              style={{ paddingLeft: `${14 + depth * 22}px` }}>
            {renaming === i.id ? (
              <RenameField item={i} code={active!} onDone={() => setRenaming(null)} />
            ) : (
            <span className={`min-w-0 flex-1 text-sm text-neutral-800 ${
              tree?.kids.get(i.id)?.length ? 'font-semibold' : ''}`}>
              {i.label}
              {tree?.kids.get(i.id)?.length ? (
                <span className="ml-1.5 text-xs font-normal text-neutral-400">
                  {tree.kids.get(i.id)!.length} inside
                </span>
              ) : null}
              {!current?.is_system_locked && (
                <button type="button" onClick={() => setRenaming(i.id)}
                        className="ml-2 text-xs text-primary-700 hover:underline">
                  Rename
                </button>
              )}
            </span>
            )}
            <span className="font-mono text-xs text-neutral-400">{i.code}</span>
            {current?.is_hierarchical && !current.is_system_locked && i.is_active && depth < MAX_DEPTH - 1 && (
              <button type="button" onClick={() => { setUnder(i); setDraft({ code: '', label: '' }) }}
                      title={`Add an entry inside ${i.label}`}
                      className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50">
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                Add inside
              </button>
            )}
            {active === PICTOGRAM_VOCABULARY && <PictogramSlot code={active} item={i} />}
            {active === 'consequence_type' && <SeverityWording code={active} item={i} />}
            {active === 'body_part' && i.is_active && <BodyRegionSelect code={active} item={i} />}
            <button
              type="button"
              disabled={toggle.isPending || (current?.is_system_locked && i.is_active)}
              onClick={() => { if (!toggle.isPending) toggle.mutate({ id: i.id, is_active: !i.is_active }) }}
              className="min-h-[32px] rounded-md px-2 text-xs font-medium text-neutral-600 disabled:opacity-30"
            >
              {i.is_active ? (tree?.kids.get(i.id)?.length ? 'Retire with what is inside' : 'Retire') : 'Restore'}
            </button>
          </li>
        ))}
        {items.data?.length === 0 && (
          <li className="px-3.5 py-6 text-center text-sm text-neutral-500">
            This list is empty. HSE supplies its entries.
          </li>
        )}
      </ul>
    </div>
  )
}

function PictogramSlot({ code, item }: { code: string; item: Item }) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const fileId = item.attrs?.pictogram_file_id
  const path = `/api/v1/settings/vocabularies/${code}/items/${item.id}/pictogram`

  // ★ Was ['vocab-items'], which no query uses: a new pictogram did not show.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['vocabularies'] })
    queryClient.invalidateQueries({ queryKey: ['vocabulary'] })
  }

  // The artwork is proxied by ehs-api and needs the session token, which an
  // `<img src>` cannot send — so it is fetched as a blob. Keyed on the file id
  // rather than the item: replacing the artwork must replace the thumbnail, or
  // somebody changes a pictogram, sees the old one, and prints it believing it
  // changed.
  const [preview, setPreview] = useState<string | null>(null)
  useEffect(() => {
    if (!fileId) { setPreview(null); return }
    let url: string | null = null
    let dropped = false
    apiBlob(path)
      .then((blob) => {
        if (dropped) return
        url = URL.createObjectURL(blob)
        setPreview(url)
      })
      .catch(() => { if (!dropped) setPreview(null) })
    return () => { dropped = true; if (url) URL.revokeObjectURL(url) }
  }, [fileId, path])

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(path, body)
    },
    onSuccess: refresh,
  })
  const remove = useMutation({ mutationFn: () => api.del(path), onSuccess: refresh })

  return (
    <span className="flex items-center gap-2">
      {fileId ? (
        <img
          src={preview ?? undefined}
          alt={`${item.label} pictogram`}
          className="h-9 w-9 rounded border border-neutral-200 bg-white object-contain"
        />
      ) : (
        <span className="flex h-9 w-9 items-center justify-center rounded border border-dashed border-warning-500 text-[11px] leading-tight text-warning-700">
          none
        </span>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload.mutate(file)
          e.target.value = ''
        }}
      />
      <button
        type="button"
        disabled={upload.isPending}
        onClick={() => inputRef.current?.click()}
        className="min-h-[32px] rounded-md px-2 text-xs font-medium text-primary-700 disabled:opacity-40"
      >
        {upload.isPending ? 'Uploading…' : fileId ? 'Replace' : 'Upload'}
      </button>
      {fileId && (
        <button
          type="button"
          disabled={remove.isPending}
          onClick={() => { if (!remove.isPending) remove.mutate() }}
          className="min-h-[32px] rounded-md px-1.5 text-xs text-neutral-500 disabled:opacity-40"
        >
          Remove
        </button>
      )}
    </span>
  )
}

function RenameField({ item, code, onDone }: { item: Item; code: string; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [label, setLabel] = useState(item.label)
  const save = useMutation({
    mutationFn: () => api.patch(
      `/api/v1/settings/vocabularies/${code}/items/${item.id}`, { label: label.trim() }),
    onSuccess: () => {
      // ★ Was ['vocab-items'], which no query uses: a rename did not show.
      queryClient.invalidateQueries({ queryKey: ['vocabularies'] })
      queryClient.invalidateQueries({ queryKey: ['vocabulary'] })
      onDone()
    },
  })
  return (
    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
      <input
        autoFocus
        className="min-h-[32px] min-w-0 flex-1 rounded border border-neutral-300 px-2 text-sm"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && label.trim()) save.mutate()
          if (e.key === 'Escape') onDone()
        }}
      />
      <button type="button" disabled={!label.trim() || save.isPending}
              onClick={() => { if (!save.isPending) save.mutate() }}
              className="min-h-[32px] rounded-md px-2 text-xs font-medium text-primary-700 disabled:opacity-40">
        Save
      </button>
      <button type="button" onClick={onDone}
              className="min-h-[32px] rounded-md px-2 text-xs text-neutral-500">
        Cancel
      </button>
      {save.isError && (
        <span role="alert" className="basis-full text-xs text-danger-700">{(save.error as Error).message}</span>
      )}
    </span>
  )
}


/**
 * What each level of "how bad" means when a risk is judged on this.
 *
 * ★ Round 8, question 5: "Judged on" is HSE's list, Equipment included. The
 * matrix kept its wording in one column per consequence, so a new one had
 * nowhere to keep any — and a consequence with no wording is not offered on
 * the assessment, because "severity 3" means nothing without it.
 */
function SeverityWording({ code, item }: { code: string; item: Item }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<Record<string, string>>(item.attrs?.severity ?? {})
  const written = Object.values(item.attrs?.severity ?? {}).filter((v) => v && v.trim()).length
  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/settings/vocabularies/${code}/items/${item.id}`, {
      attrs: {
        ...(item.attrs ?? {}),
        severity: Object.fromEntries(Object.entries(text).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()])),
      },
    }),
    onSuccess: () => {
      setOpen(false)
      queryClient.invalidateQueries({ queryKey: ['vocabularies'] })
      queryClient.invalidateQueries({ queryKey: ['risk-matrix'] })
    },
  })
  return (
    <>
      <button type="button" onClick={() => { setText(item.attrs?.severity ?? {}); setOpen(true) }}
              className="min-h-[32px] rounded-md px-2 text-xs font-medium text-primary-700 hover:underline">
        How-bad wording ({written}/6)
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-4">
            <h3 className="text-sm font-semibold text-neutral-900">How bad — judged on {item.label}</h3>
            <p className="mt-1 text-xs text-neutral-500">
              What each level means for this consequence. A level left empty cannot be chosen;
              with none written, this is not offered on a risk assessment.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {['5', '4', '3', '2', '1', '0'].map((lv) => (
                <label key={lv} className="flex items-center gap-2 text-sm">
                  <span className="w-6 font-mono text-xs text-neutral-500">{lv}</span>
                  <input className="min-h-[38px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
                         aria-label={`Level ${lv}`} value={text[lv] ?? ''}
                         onChange={(e) => setText({ ...text, [lv]: e.target.value })} />
                </label>
              ))}
            </div>
            {save.isError && <p className="mt-2 text-xs text-danger-700">{(save.error as Error).message}</p>}
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" className="min-h-[38px] rounded-lg border border-neutral-300 px-3 text-sm"
                      onClick={() => setOpen(false)} disabled={save.isPending}>Cancel</button>
              <button type="button" className="min-h-[38px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50"
                      disabled={save.isPending} onClick={() => { if (!save.isPending) save.mutate() }}>
                {save.isPending ? 'Saving…' : 'Save the wording'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/**
 * Where a body part sits on the dashboard's body picture.
 *
 * Round 10, q10-body: shade each part "using the body part list under
 * Settings → Lists". Seeded entries are placed by their code; this places any
 * entry, moves one, or keeps it off the picture. ★ attrs are replaced whole on
 * save, so the rest of them travel with the change.
 */
function BodyRegionSelect({ code, item }: { code: string; item: Item }) {
  const queryClient = useQueryClient()
  const regions = useQuery({
    queryKey: ['dashboard', 'settings'],
    queryFn: () => api.get<{ regions: { key: string; label: string }[] }>('/api/v1/dashboard/settings'),
    staleTime: 10 * 60_000,
  })
  const save = useMutation({
    mutationFn: (region: string) => {
      const { body_region: _old, ...rest } = item.attrs ?? {}
      return api.patch(`/api/v1/settings/vocabularies/${code}/items/${item.id}`, {
        attrs: region ? { ...rest, body_region: region } : rest,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vocabularies'] })
      queryClient.invalidateQueries({ queryKey: ['vocabulary'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  // What the select shows is where it is drawn now; "" only when the entry
  // has never been placed and its code places it nowhere.
  const value = item.attrs?.body_region === 'none' ? 'none' : (item.body_region ?? '')
  return (
    <label className="flex items-center gap-1 text-xs text-neutral-500">
      On the picture
      <select value={value} disabled={save.isPending || regions.isPending}
              aria-label={`Where ${item.label} is on the body picture`}
              onChange={(e) => save.mutate(e.target.value)}
              className="min-h-[32px] rounded-md border border-neutral-300 bg-white px-1.5 text-xs text-neutral-800">
        {value === '' && <option value="">Not placed</option>}
        <option value="none">Not on the picture</option>
        {(regions.data?.regions ?? []).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
      </select>
      {save.isError && <span className="text-danger-700" role="alert">Not saved</span>}
    </label>
  )
}
