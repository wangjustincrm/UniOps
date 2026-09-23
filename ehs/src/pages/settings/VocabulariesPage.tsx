/**
 * The lists HSE maintains.
 *
 * Retiring an entry is the only form of removal there is, and the screen says
 * so rather than offering a delete that quietly means something else.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Plus } from 'lucide-react'
import { api, apiBlob, ApiError } from '@/lib/api'
import { PageHeader } from '@/components/ui'

interface Vocabulary {
  code: string; name: string; description: string | null
  is_hierarchical: boolean; is_system_locked: boolean
  item_count: number; active_item_count: number
}
interface Item {
  id: string; code: string; label: string; sort_order: number
  is_active: boolean; parent_id: string | null; path: string | null
  attrs?: { pictogram_file_id?: string; pictogram_filename?: string }
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
  }
  const add = useMutation({
    mutationFn: () => api.post(`/api/v1/settings/vocabularies/${active}/items`, draft),
    onSuccess: () => { setDraft({ code: '', label: '' }); invalidate() },
  })
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
            onClick={() => setSelected(v.code)}
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

      {current && !current.is_system_locked && (
        <form
          className="mb-3 flex flex-wrap gap-2"
          onSubmit={(e) => { e.preventDefault(); if (!add.isPending) add.mutate() }}
        >
          <input
            className="min-h-[44px] w-28 rounded-lg border border-neutral-300 px-2.5 text-sm"
            placeholder="CODE" value={draft.code}
            onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })}
          />
          <input
            className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
            placeholder="What it says on the form" value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
          <button
            type="submit"
            disabled={add.isPending || !draft.code.trim() || !draft.label.trim()}
            className="flex min-h-[44px] items-center gap-1.5 rounded-lg bg-primary-600 px-3.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            <Plus className="h-4 w-4" aria-hidden />
            Add
          </button>
        </form>
      )}
      {add.isError && (
        <p className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {(add.error as ApiError).message}
        </p>
      )}

      <label className="mb-2 flex min-h-[36px] items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" className="h-3.5 w-3.5" checked={showRetired}
               onChange={(e) => setShowRetired(e.target.checked)} />
        Show retired entries
      </label>

      <ul className="divide-y divide-neutral-100 rounded-xl border border-neutral-200 bg-white">
        {items.data?.map((i) => (
          <li key={i.id} className={`flex items-center gap-3 px-3.5 py-2.5 ${i.is_active ? '' : 'opacity-50'}`}>
            {renaming === i.id ? (
              <RenameField item={i} code={active!} onDone={() => setRenaming(null)} />
            ) : (
            <span className="min-w-0 flex-1 text-sm text-neutral-800">
              {i.path && i.path !== i.label ? (
                <span className="text-neutral-400">{i.path.replace(/\/[^/]*$/, '/')}</span>
              ) : null}
              {i.label}
              {!current?.is_system_locked && (
                <button type="button" onClick={() => setRenaming(i.id)}
                        className="ml-2 text-xs text-primary-700 hover:underline">
                  Rename
                </button>
              )}
            </span>
            )}
            <span className="font-mono text-xs text-neutral-400">{i.code}</span>
            {active === PICTOGRAM_VOCABULARY && <PictogramSlot code={active} item={i} />}
            <button
              type="button"
              disabled={toggle.isPending || (current?.is_system_locked && i.is_active)}
              onClick={() => { if (!toggle.isPending) toggle.mutate({ id: i.id, is_active: !i.is_active }) }}
              className="min-h-[32px] rounded-md px-2 text-xs font-medium text-neutral-600 disabled:opacity-30"
            >
              {i.is_active ? 'Retire' : 'Restore'}
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

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['vocab-items'] })

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
      queryClient.invalidateQueries({ queryKey: ['vocab-items'] })
      onDone()
    },
  })
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2">
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
    </span>
  )
}
