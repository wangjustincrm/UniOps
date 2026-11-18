import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2 } from 'lucide-react'
import { SortTh, useSortableRows, Input, Select, TBody, THead, Table, Td, Th, Tr, Button, Modal as ShellModal } from '@uniops/shell'
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
        <Button size="md" onClick={openCreate}
         >
          <Plus className="h-4 w-4" />Add Unit
        </Button>
      </div>
      {listError && <p role="alert" className="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{listError}</p>}
      {dlg.dialog}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-fg-muted">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-fg-muted">No units yet.</div>
        ) : (
          <Table>
            <THead>
              <Tr>
                <SortTh label="Code" sortKey="code" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Name" sortKey="name" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Dimension" sortKey="dimension" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Status" sortKey="status" sort={uoms.sort} onSort={uoms.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <Th />
              </Tr>
            </THead>
            <TBody>
              {uoms.rows.map((u, i) => (
                <Tr key={u.id} className={cn('border-b border-line-subtle', i === uoms.rows.length - 1 && 'border-b-0')}>
                  <Td className="font-mono text-xs text-fg-secondary">{u.code}</Td>
                  <Td className="text-fg">{u.name}</Td>
                  <Td className="text-xs text-fg-muted">{u.dimension}</Td>
                  <Td>
                    <span className={cn('rounded-full px-2 py-0.5 text-2xs font-medium', u.is_active ? 'bg-success-soft text-success-fg' : 'bg-surface-sunken text-fg-muted')}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </Td>
                  <Td>
                    <div className="flex items-center justify-end gap-1">
                      <Button aria-label="Edit" variant="subtle" size="icon-sm" onClick={() => openEdit(u)}><Pencil className="h-3.5 w-3.5" /></Button>
                      <Button variant="danger-ghost" size="icon-sm" aria-label="Remove" onClick={() => handleDelete(u)}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </div>

      {modal && (
        <ShellModal open onClose={() => setModal(null)} title={modal.mode === 'create' ? 'Add Unit' : 'Edit Unit'} closeOnEscape={false} size="full" className="max-w-sm">
          <form onSubmit={handleSave} className="flex flex-col gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Code</label>
              <Input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                className="w-full"
                placeholder="kg" required />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Name</label>
              <Input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                className="w-full"
                placeholder="Kilogram" required />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Dimension</label>
              <Select value={form.dimension} onChange={(e) => setForm((p) => ({ ...p, dimension: e.target.value as Dimension }))}
                className="w-full">
                {DIMENSIONS.map((d) => <option key={d} value={d}>{d}</option>)}
              </Select>
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
              <Button variant="secondary" size="md" type="button" onClick={() => setModal(null)}>Cancel</Button>
              <Button size="md" type="submit" disabled={saving}
               >
                {saving ? 'Saving…' : 'Save Changes'}
              </Button>
            </div>
          </form>
        </ShellModal>
      )}
    </div>
  )
}
