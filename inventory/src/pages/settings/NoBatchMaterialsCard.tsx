// Materials NC stopped tracking by batch (design D11): from `since_date` on,
// their WMS-vs-NC difference is explained as "NC no longer tracks batches"
// and they are never expanded by batch.
//
// since_date is a plain date. It is shown and submitted as the "YYYY-MM-DD"
// string, never passed through new Date() (which reads it as UTC midnight and
// shows the previous day at UTC-4).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label, SortTh, useSortableRows } from '@uniops/shell'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import type { useToasts } from '@/hooks/useToasts'
import { settingsApi, type NoBatchMaterial } from './settingsApi'

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

interface FormState {
  /** null = adding a new entry; otherwise the code being edited (fixed). */
  editing: string | null
  code: string
  since: string
  note: string
}

const EMPTY: FormState = { editing: null, code: '', since: '', note: '' }

function EntryForm({ initial, existing, onDone, toasts }: {
  initial: FormState
  existing: Set<string>
  onDone: () => void
  toasts: ReturnType<typeof useToasts>
}) {
  const queryClient = useQueryClient()
  const [f, setF] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const code = (f.editing ?? f.code).trim().toUpperCase()

  const save = useMutation({
    mutationFn: () => settingsApi.putNoBatchMaterial(code, f.since, f.note.trim() || null),
    onMutate: () => setError(null),
    onSuccess: () => {
      toasts.success(`${code} saved.`)
      onDone()
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'Save failed.'),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['inv-no-batch'] }),
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (save.isPending) return
    if (!code) return setError('Material code is required.')
    if (!f.editing && existing.has(code)) return setError(`${code} is already on the list — edit it instead.`)
    if (!DATE_RE.test(f.since)) return setError('Since date is required (YYYY-MM-DD).')
    save.mutate()
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[12rem_12rem_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor="nb-code">Material code</Label>
          <Input
            id="nb-code" value={f.editing ?? f.code} readOnly={f.editing != null} maxLength={60}
            onChange={(e) => setF({ ...f, code: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="nb-since">Since date</Label>
          {/* <input type="date"> yields "YYYY-MM-DD" directly — no Date object. */}
          <Input
            id="nb-since" type="date" value={f.since}
            onChange={(e) => setF({ ...f, since: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="nb-note">Note</Label>
          <Input
            id="nb-note" value={f.note} maxLength={500}
            onChange={(e) => setF({ ...f, note: e.target.value })}
          />
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-danger-600">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending && <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />}
          {f.editing ? 'Save' : 'Add'}
        </Button>
      </div>
    </form>
  )
}

export function NoBatchMaterialsCard({ canAdmin, toasts }: {
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
}) {
  const queryClient = useQueryClient()
  const q = useQuery({ queryKey: ['inv-no-batch'], queryFn: settingsApi.noBatchMaterials })
  const [form, setForm] = useState<FormState | null>(null)
  const [deleting, setDeleting] = useState<NoBatchMaterial | null>(null)

  const del = useMutation({
    mutationFn: (code: string) => settingsApi.deleteNoBatchMaterial(code),
    onSuccess: (_d, code) => {
      toasts.success(`${code} removed.`)
      setDeleting(null)
    },
    onError: (err) => toasts.error(err instanceof Error ? err.message : 'Delete failed.'),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['inv-no-batch'] }),
  })

  const items = q.data ?? []
  const existing = new Set(items.map((i) => i.material_code))
  const { rows, sort, onSort } = useSortableRows(q.data, {
    code: (m) => m.material_code, since: (m) => m.since_date, note: (m) => m.note,
  })
  const th = 'px-3 py-2 font-medium'

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Materials not tracked by batch in NC</CardTitle>
          <p className="mt-0.5 text-xs text-neutral-500">
            From the since date on, these are compared by material only and never expanded by batch.
          </p>
        </div>
        {canAdmin && !form && (
          <Button size="sm" variant="secondary" onClick={() => setForm(EMPTY)}>
            <Plus aria-hidden className="h-3.5 w-3.5" /> Add material
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {form && (
          <EntryForm
            key={form.editing ?? '__new'}
            initial={form} existing={existing} toasts={toasts}
            onDone={() => setForm(null)}
          />
        )}
        {q.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
        {q.isError && <p className="text-sm text-danger-600">Could not load the list: {(q.error as Error).message}</p>}
        {q.data && items.length === 0 && <p className="text-sm text-neutral-500">No materials on the list.</p>}
        {items.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50">
                <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
                  <SortTh label="Material code" sortKey="code" sort={sort} onSort={onSort} className={th} />
                  <SortTh label="Since" sortKey="since" sort={sort} onSort={onSort} className={th} />
                  <SortTh label="Note" sortKey="note" sort={sort} onSort={onSort} className={th} />
                  {canAdmin && <th className="px-3 py-2" />}
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.material_code} className="border-b border-neutral-100 last:border-0">
                    <td className="px-3 py-2 font-mono text-xs">{m.material_code}</td>
                    <td className="px-3 py-2 tabular-nums">{m.since_date}</td>
                    <td className="px-3 py-2 text-neutral-600">{m.note || '—'}</td>
                    {canAdmin && (
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1">
                          <Button
                            size="icon-sm" variant="ghost" aria-label={`Edit ${m.material_code}`}
                            onClick={() => setForm({ editing: m.material_code, code: m.material_code, since: m.since_date, note: m.note ?? '' })}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            size="icon-sm" variant="ghost" aria-label={`Remove ${m.material_code}`}
                            className="text-danger-600"
                            onClick={() => setDeleting(m)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
      {deleting && (
        <ConfirmDialog
          title="Remove material"
          confirmLabel="Remove"
          danger
          busy={del.isPending}
          onCancel={() => { if (!del.isPending) setDeleting(null) }}
          onConfirm={() => { if (!del.isPending) del.mutate(deleting.material_code) }}
        >
          <p>
            Remove <span className="font-mono">{deleting.material_code}</span> (since {deleting.since_date}) from the list?
          </p>
          <p className="text-neutral-500">
            Its WMS-vs-NC difference will no longer be explained as "NC does not track batches".
          </p>
        </ConfirmDialog>
      )}
    </Card>
  )
}
