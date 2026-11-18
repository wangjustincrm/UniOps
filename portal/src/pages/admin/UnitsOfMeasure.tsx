import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, X } from 'lucide-react'
import { SortTh, useSortableRows } from '@uniops/shell'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useConfirmDialog } from '@/components/ConfirmDialog'

type Dimension = 'count' | 'mass' | 'volume' | 'length' | 'area' | 'time' | 'other'
const DIMENSIONS: Dimension[] = ['count', 'mass', 'volume', 'length', 'area', 'time', 'other']

interface ApiUom {
  id: string
  code: string
  name: string
  dimension: Dimension
  is_active: boolean
}

function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-line-subtle pb-4">
      <h2 className="text-lg font-semibold text-fg">{title}</h2>
      <p className="mt-0.5 text-sm text-fg-muted">{description}</p>
    </div>
  )
}

export function UnitsOfMeasure() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<{ items: ApiUom[]; total: number }>({
    queryKey: ['portal-uoms'],
    queryFn: () => mdmApi.get<{ items: ApiUom[]; total: number }>('/uom'),
  })
  const uoms = useSortableRows(data?.items, {
    code: (u) => u.code, name: (u) => u.name, dimension: (u) => u.dimension,
    status: (u) => (u.is_active ? 'Active' : 'Inactive'),
  })
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; uom?: ApiUom } | null>(null)
  const [form, setForm] = useState<{ code: string; name: string; dimension: Dimension; is_active: boolean }>(
    { code: '', name: '', dimension: 'count', is_active: true },
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const dlg = useConfirmDialog()

  const openCreate = () => { setForm({ code: '', name: '', dimension: 'count', is_active: true }); setError(''); setModal({ mode: 'create' }) }
  const openEdit = (u: ApiUom) => { setForm({ code: u.code, name: u.name, dimension: u.dimension, is_active: u.is_active }); setError(''); setModal({ mode: 'edit', uom: u }) }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault(); setError(''); setSaving(true)
    try {
      if (modal?.mode === 'create') {
        await mdmApi.post('/uom', form)
      } else {
        await mdmApi.patch(`/uom/${modal?.uom?.id}`, form)
      }
      qc.invalidateQueries({ queryKey: ['portal-uoms'] })
      setModal(null)
    } catch (err: any) { setError(err.message) } finally { setSaving(false) }
  }

  // Asked in-page; a refusal shows above the table (both were browser pop-ups).
  const handleDelete = async (u: ApiUom) => {
    if (!(await dlg.confirm({ title: 'Delete unit', message: `Delete unit "${u.code}"?`, confirmLabel: 'Delete' }))) return
    setListError('')
    try {
      await mdmApi.delete(`/uom/${u.id}`)
      qc.invalidateQueries({ queryKey: ['portal-uoms'] })
    } catch (err: any) { setListError(err.message) }
  }

  return (
    <div>
      <SectionHeader title="Units of Measure" description="Units available in line-item pickers across EPMS (PR/PO/Parts) and OA. Shared master data." />
      <div className="mb-4 flex justify-end">
        <button onClick={openCreate}
          className="flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800">
          <Plus className="h-4 w-4" />Add Unit
        </button>
      </div>
      {listError && <p role="alert" className="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{listError}</p>}
      {dlg.dialog}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-fg-muted">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-fg-muted">No units yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-line-subtle bg-surface-subtle">
              <tr>
                <SortTh label="Code" sortKey="code" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted" />
                <SortTh label="Name" sortKey="name" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted" />
                <SortTh label="Dimension" sortKey="dimension" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted" />
                <SortTh label="Status" sortKey="status" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted" />
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted" />
              </tr>
            </thead>
            <tbody>
              {uoms.rows.map((u, i) => (
                <tr key={u.id} className={cn('border-b border-line-subtle', i === uoms.rows.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-fg-secondary">{u.code}</td>
                  <td className="px-4 py-3 text-fg">{u.name}</td>
                  <td className="px-4 py-3 text-xs text-fg-muted">{u.dimension}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-2xs font-medium', u.is_active ? 'bg-success-soft text-success-fg' : 'bg-surface-sunken text-fg-muted')}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(u)} className="rounded p-1.5 text-fg-muted hover:bg-surface-sunken hover:text-fg-secondary"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => handleDelete(u)} className="rounded p-1.5 text-fg-muted hover:bg-danger-soft hover:text-danger-fg"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl border border-line bg-surface shadow-xl">
            <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
              <h3 className="font-semibold text-fg">{modal.mode === 'create' ? 'Add Unit' : 'Edit Unit'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-fg-muted hover:bg-surface-sunken"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Code</label>
                <input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                  className="w-full rounded-lg border border-line px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="kg" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Name</label>
                <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full rounded-lg border border-line px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="Kilogram" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Dimension</label>
                <select value={form.dimension} onChange={(e) => setForm((p) => ({ ...p, dimension: e.target.value as Dimension }))}
                  className="w-full rounded-lg border border-line px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                  {DIMENSIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-fg-secondary">Active</span>
                <button type="button" onClick={() => setForm((p) => ({ ...p, is_active: !p.is_active }))}
                  className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.is_active ? 'bg-primary-600' : 'bg-surface-strong')}>
                  <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-surface shadow transition-transform', form.is_active ? 'translate-x-4.5' : 'translate-x-0.5')} />
                </button>
              </div>
              {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>}
              <div className="flex justify-end gap-2 border-t border-line-subtle pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-line px-4 py-2 text-sm text-fg-secondary hover:bg-surface-subtle">Cancel</button>
                <button type="submit" disabled={saving}
                  className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-60">
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
