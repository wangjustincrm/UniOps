import { useState, useEffect, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api, epmsApi, budgetApi } from '@/lib/api'
import { isEditable } from '@/lib/status'
import { useOaAuth } from '@/store/auth'

interface Pa {
  id: string; pa_number: string; title: string; status: string
  vendor_id: string; vendor_name: string
  budget_account_code: string | null; cost_center_id: string | null
  notes: string | null
}
interface DbVendor { id: string; name: string; code: string }
interface CostCenter { id: string; code: string; name: string; is_active: boolean }
interface BudgetL2 { id: string; code: string; name: string }
interface BudgetL1 { id: string; code: string; name: string; accounts: BudgetL2[] }

export default function PaDirectEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(oaRoutes)
  const { user } = useOaAuth()
  const deptId = user?.department_id ?? null

  const { data: pa, isLoading } = useQuery<Pa>({
    queryKey: ['pa', id],
    queryFn: () => api.get<Pa>(`/api/v1/pa/${id}`),
    enabled: !!id,
  })

  const { data: costCenters = [] } = useQuery<CostCenter[]>({
    queryKey: ['epms-cost-centers', deptId],
    queryFn: () => epmsApi.get<CostCenter[]>(
      deptId ? `/api/v1/cost-centers?active_only=true&department_id=${deptId}`
             : '/api/v1/cost-centers?active_only=true'),
  })
  const { data: budgetHierarchy } = useQuery<{ l1_groups: BudgetL1[] }>({
    queryKey: ['budget-hierarchy'],
    queryFn: () => budgetApi.get<{ l1_groups: BudgetL1[] }>('/hierarchy'),
  })
  const l1Groups = budgetHierarchy?.l1_groups ?? []

  const [title, setTitle] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [vendorName, setVendorName] = useState('')
  const [selectedCostCenterId, setSelectedCostCenterId] = useState('')
  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL2, setSelectedL2] = useState('')
  const [notes, setNotes] = useState('')
  const [vendorSearch, setVendorSearch] = useState('')
  const [vendorSearchOpen, setVendorSearchOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Prefill once the PA and budget hierarchy load.
  useEffect(() => {
    if (!pa) return
    setTitle(pa.title)
    setVendorId(pa.vendor_id)
    setVendorName(pa.vendor_name)
    setSelectedCostCenterId(pa.cost_center_id ?? '')
    setNotes(pa.notes ?? '')
  }, [pa])
  useEffect(() => {
    if (!pa?.budget_account_code || l1Groups.length === 0) return
    const l1 = l1Groups.find(g => g.accounts.some(a => a.code === pa.budget_account_code))
    if (l1) { setSelectedL1(l1.code); setSelectedL2(pa.budget_account_code!) }
  }, [pa, l1Groups])

  const { data: vendorsData } = useQuery<{ items: DbVendor[] }>({
    queryKey: ['vendors-search', vendorSearch],
    queryFn: () => {
      const p = new URLSearchParams({ active_only: 'true', page_size: '50' })
      if (vendorSearch) p.set('search', vendorSearch)
      return api.get<{ items: DbVendor[] }>(`/api/v1/vendors?${p}`)
    },
    enabled: vendorSearchOpen,
  })
  const vendors = vendorsData?.items ?? []
  const l2Accounts = l1Groups.find(l1 => l1.code === selectedL1)?.accounts ?? []

  if (isLoading) return (
    <div className="flex items-center justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-400" /></div>
  )
  if (!pa) return <div className="py-16 text-center text-sm text-danger-500">Payment application not found</div>
  if (!isEditable(pa.status)) return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <a href={`/pa/${id}`} className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700">
        <ArrowLeft className="h-4 w-4" />Back to PA
      </a>
      <p className="rounded-lg border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-700">
        This payment application is {pa.status} and can no longer be edited.
      </p>
    </div>
  )

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true); setError('')
    try {
      await api.patch(`/api/v1/pa/${id}`, {
        title: title.trim(),
        vendor_id: vendorId || null,
        vendor_name: vendorName || null,
        budget_account_code: selectedL2 || null,
        cost_center_id: selectedCostCenterId || null,
        notes: notes || null,
      })
      replaceTab(`/pa/${id}`)
    } catch (err: any) {
      setError(err.message || 'Failed to save changes')
    } finally { setSaving(false) }
  }

  const input = 'w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400'
  const sel = 'h-9 w-full rounded-md border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500'

  return (
    <form onSubmit={save} className="flex flex-col gap-5 max-w-2xl">
      <div>
        <a href={`/pa/${id}`} className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to PA
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Edit Payment Application</h1>
        <p className="mt-0.5 text-sm text-neutral-500 font-mono">{pa.pa_number}</p>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Title *</label>
        <input value={title} onChange={e => setTitle(e.target.value)} required className={input} />
      </div>

      {/* Description — background / reason for this payment */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Description</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
          placeholder="Background and reason for this payment…" className={`${input} resize-none`} />
      </div>

      {/* Matched Vendor */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Matched Vendor</label>
        <div className="flex items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
          <span className="text-sm text-neutral-800">{vendorName || '—'}</span>
          <button type="button" onClick={() => { setVendorSearchOpen(o => !o); setVendorSearch('') }}
            className="text-xs text-neutral-400 hover:text-neutral-600 underline shrink-0">Change</button>
        </div>
        {vendorSearchOpen && (
          <div className="relative mt-1">
            <input autoFocus placeholder="Search vendor by name or code…" value={vendorSearch}
              onChange={e => setVendorSearch(e.target.value)} className={input} />
            <div className="absolute z-20 mt-1 left-0 right-0 rounded-lg border border-neutral-200 bg-white shadow-lg max-h-48 overflow-y-auto">
              {vendors.length ? vendors.map(v => (
                <button key={v.id} type="button"
                  onClick={() => { setVendorId(v.id); setVendorName(v.name); setVendorSearchOpen(false) }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left">
                  <span className="font-mono text-xs bg-neutral-100 rounded px-1.5 py-0.5 text-neutral-600 shrink-0">{v.code}</span>
                  {v.name}
                </button>
              )) : <p className="px-3 py-2 text-xs text-neutral-400">No vendors match your search</p>}
            </div>
          </div>
        )}
      </div>

      {/* Cost Center & Budget Account */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-2">
        <label className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Cost Center & Budget Account</label>
        <select value={selectedCostCenterId} onChange={e => setSelectedCostCenterId(e.target.value)} className={sel}>
          <option value="">Select Cost Center…</option>
          {costCenters.map(cc => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
        </select>
        <select value={selectedL1} onChange={e => { setSelectedL1(e.target.value); setSelectedL2('') }} className={sel}>
          <option value="">Select L1 Category…</option>
          {l1Groups.map(l1 => <option key={l1.id} value={l1.code}>{l1.code} — {l1.name}</option>)}
        </select>
        <select value={selectedL2} onChange={e => setSelectedL2(e.target.value)} disabled={!selectedL1}
          className={`${sel} disabled:bg-neutral-50 disabled:text-neutral-400`}>
          <option value="">Select L2 Account…</option>
          {l2Accounts.map(a => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
        </select>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      <button type="submit" disabled={saving || !title.trim()}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors">
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
        {saving ? 'Saving…' : 'Save Changes'}
      </button>
    </form>
  )
}
