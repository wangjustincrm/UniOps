/**
 * What has to be confirmed before each kind of permit is issued.
 *
 * Round 7 (answer box): "on permits, in the before work starts section, create
 * a list to select the items to be confirmed before the permit can be raised.
 * allow this list to be modified in the settings section." The list applies to
 * permits not yet issued; an issued permit keeps the wording it was issued
 * against, so editing here never rewrites one.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import {
  Card, ErrorNote, inputClass, LoadFailedNote, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'

interface Item { key?: string; label: string; help?: string }
interface PermitType { id: string; code: string; name: string; precautions: Item[] }

export default function PermitChecksPage() {
  const types = useQuery({
    queryKey: ['permit-types'],
    queryFn: () => api.get<PermitType[]>('/api/v1/permit-types'),
  })
  return (
    <div className="p-4">
      <PageHeader title="Permit checks"
                  subtitle="What has to be confirmed before each kind of permit is issued." />
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        A change applies to permits still being prepared. A permit already issued keeps the
        wording it was issued against. A check whose wording is changed has to be confirmed
        again on permits being prepared; changing only its help text does not.
      </p>
      {types.isError && <LoadFailedNote what="The permit types" />}
      {types.isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      <div className="flex flex-col gap-3">
        {(types.data ?? []).map((t) => <TypeChecks key={t.id} type={t} />)}
      </div>
    </div>
  )
}

function TypeChecks({ type }: { type: PermitType }) {
  const queryClient = useQueryClient()
  const [items, setItems] = useState<Item[]>(type.precautions ?? [])
  const [dirty, setDirty] = useState(false)
  const change = (next: Item[]) => { setItems(next); setDirty(true) }
  const save = useMutation({
    mutationFn: () => api.put(`/api/v1/permit-types/${type.id}/precautions`, {
      precautions: items.filter((i) => i.label.trim()).map((i) => ({
        key: i.key ?? null, label: i.label.trim(), help: i.help?.trim() || null,
      })),
    }),
    onSuccess: () => { setDirty(false); queryClient.invalidateQueries({ queryKey: ['permit-types'] }) },
  })
  const usable = items.filter((i) => i.label.trim().length >= 2).length
  return (
    <Card>
      <SectionTitle className="mb-2">{type.name}</SectionTitle>
      <ul className="flex flex-col gap-1.5">
        {items.map((item, i) => (
          <li key={item.key ?? `new-${i}`} className="flex items-center gap-2">
            <input className={inputClass} value={item.label} aria-label={`Check ${i + 1}`}
                   onChange={(e) => change(items.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
            <button type="button" aria-label={`Remove ${item.label || 'this check'}`}
                    onClick={() => change(items.filter((_, j) => j !== i))}
                    className="shrink-0 rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600">
              <X className="h-4 w-4" />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <button type="button" className={secondaryButton}
                onClick={() => change([...items, { label: '' }])}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a check
        </button>
        {dirty && (
          <button type="button" className={primaryButton}
                  disabled={usable === 0 || save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save the checks'}
          </button>
        )}
      </div>
      {save.isSuccess && !dirty && <p className="mt-1 text-xs text-success-700">Saved.</p>}
      <ErrorNote error={save.error} />
    </Card>
  )
}
