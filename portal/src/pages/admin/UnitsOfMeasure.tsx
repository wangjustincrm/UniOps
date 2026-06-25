import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, X } from 'lucide-react'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'

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
    <div className="mb-6 border-b border-neutral-100 pb-4">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{description}</p>
    </div>
  )
}

export function UnitsOfMeasure() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<{ items: ApiUom[]; total: number }>({
    queryKey: ['portal-uoms'],
    queryFn: () => mdmApi.get<{ items: ApiUom[]; total: number }>('/uom'),
  })
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; uom?: ApiUom } | null>(null)
  const [form, setForm] = useState<{ code: string; name: string; dimension: Dimension; is_active: boolean }>(
    { code: '', name: '', dimension: 'count', is_active: true },
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

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

  const handleDelete = async (u: ApiUom) => {
    if (!confirm(`Delete unit "${u.code}"?`)) return
    try {
      await mdmApi.delete(`/uom/${u.id}`)
      qc.invalidateQueries({ queryKey: ['portal-uoms'] })
    } catch (err: any) { alert(err.message) }
  }

  return (
    <div>
      <SectionHeader title="Units of Measure" description="Units available in line-item pickers across EPMS (PR/PO/Parts) and OA. Shared master data." />
      <div className="mb-4 flex justify-end">
        <button onClick={openCreate}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A]">
          <Plus className="h-4 w-4" />Add Unit
        </button>
      </div>
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No units yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Code', 'Name', 'Dimension', 'Status', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((u, i) => (
                <tr key={u.id} className={cn('border-b border-neutral-100', i === data.items.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-700">{u.code}</td>
                  <td className="px-4 py-3 text-neutral-800">{u.name}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{u.dimension}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', u.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(u)} className="rounded p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => handleDelete(u)} className="rounded p-1.5 text-neutral-400 hover:bg-red-50 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
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
          <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
              <h3 className="font-semibold text-neutral-900">{modal.mode === 'create' ? 'Add Unit' : 'Edit Unit'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Code</label>
                <input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="kg" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
                <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="Kilogram" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Dimension</label>
                <select value={form.dimension} onChange={(e) => setForm((p) => ({ ...p, dimension: e.target.value as Dimension }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                  {DIMENSIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Active</span>
                <button type="button" onClick={() => setForm((p) => ({ ...p, is_active: !p.is_active }))}
                  className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.is_active ? 'bg-primary-600' : 'bg-neutral-200')}>
                  <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform', form.is_active ? 'translate-x-4.5' : 'translate-x-0.5')} />
                </button>
              </div>
              {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Cancel</button>
                <button type="submit" disabled={saving}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60">
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
