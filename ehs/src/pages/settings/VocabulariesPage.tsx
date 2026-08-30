/**
 * The lists HSE maintains.
 *
 * Retiring an entry is the only form of removal there is, and the screen says
 * so rather than offering a delete that quietly means something else.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

interface Vocabulary {
  code: string; name: string; description: string | null
  is_hierarchical: boolean; is_system_locked: boolean
  item_count: number; active_item_count: number
}
interface Item {
  id: string; code: string; label: string; sort_order: number
  is_active: boolean; parent_id: string | null; path: string | null
}

export default function VocabulariesPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [showRetired, setShowRetired] = useState(false)
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
      <h1 className="text-lg font-bold tracking-tight text-neutral-900">Lists</h1>
      <p className="mb-3 text-xs text-neutral-500">
        The choices that appear on forms. Entries are retired, never deleted —
        a record that used one has to keep making sense.
      </p>

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
            <span className="min-w-0 flex-1 text-sm text-neutral-800">
              {i.path && i.path !== i.label ? (
                <span className="text-neutral-400">{i.path.replace(/\/[^/]*$/, '/')}</span>
              ) : null}
              {i.label}
            </span>
            <span className="font-mono text-xs text-neutral-400">{i.code}</span>
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
