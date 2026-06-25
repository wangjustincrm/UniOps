import { useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { mdmApi, api } from '@/lib/api'

interface ErpSupplierRow {
  erp_supplier_code: string
  supplier_name: string
  supplier_tel: string | null
  supplier_address: string | null
  supplier_type: string | null
}

interface ExistingVendor {
  id: string
  erp_id: string | null
}

interface ErpVendorImportDrawerProps {
  vendorCategories: string[]
  onClose: () => void
}

export function ErpVendorImportDrawer({ vendorCategories, onClose }: ErpVendorImportDrawerProps) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [excludeImported, setExcludeImported] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const initialCategory = vendorCategories.length > 0 ? vendorCategories[0] : 'other'
  const [category, setCategory] = useState(initialCategory)
  const [paymentTerms, setPaymentTerms] = useState('net30')
  const [currency, setCurrency] = useState('CAD')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ created: number; errors: { erp_supplier_code: string; reason: string }[] } | null>(null)
  const [error, setError] = useState('')

  const { data: existingVendors } = useQuery<{ items: ExistingVendor[]; total: number }>({
    queryKey: ['vendors-for-erp-import'],
    queryFn: () => api.get('/vendors', { page: 1, page_size: 1000 }),
    enabled: excludeImported,
  })
  const importedCodes = (existingVendors?.items || [])
    .map((v) => v.erp_id)
    .filter((c): c is string => !!c)

  const params: Record<string, string | number> = { page: 1, page_size: 50 }
  if (search) params.search = search
  if (excludeImported && importedCodes.length) params.exclude_codes = importedCodes.join(',')

  const { data, isLoading } = useQuery<{ items: ErpSupplierRow[]; total: number }>({
    queryKey: ['erp-import-suppliers', search, excludeImported, importedCodes.join(',')],
    queryFn: () => mdmApi.get('/erp/suppliers', params),
  })

  const toggle = (code: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code); else next.add(code)
      return next
    })
  }

  const submit = async () => {
    setSubmitting(true); setError('')
    try {
      const resp = await api.post<{ created: number; errors: { erp_supplier_code: string; reason: string }[] }>('/vendors/import-from-erp', {
        erp_supplier_codes: Array.from(selected),
        defaults: { category, payment_terms: paymentTerms, currency },
      })
      setResult(resp)
      qc.invalidateQueries({ queryKey: ['vendors'] })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end">
      <div className="bg-white w-full max-w-3xl h-full overflow-hidden flex flex-col">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold">Import vendors from ERP</h2>
          <button onClick={onClose}><X className="h-4 w-4 text-neutral-500" /></button>
        </div>

        {result ? (
          <div className="flex-1 overflow-auto p-4 space-y-3">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm">
              <strong>{result.created} created</strong>, {result.errors.length} errors.
            </div>
            {result.errors.length > 0 && (
              <ul className="text-xs text-amber-700 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e.erp_supplier_code}: {e.reason}</li>)}
              </ul>
            )}
            <p className="text-xs text-neutral-500">Vendor contact emails are placeholders (<code>code@erp.local</code>). Update them in the vendor list.</p>
            <button onClick={onClose} className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white">Done</button>
          </div>
        ) : (
          <>
            <div className="border-b border-neutral-200 px-4 py-3 space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <label className="text-xs">Category
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={category} onChange={e => setCategory(e.target.value)}>
                    {vendorCategories.map(c => <option key={c} value={c}>{c}</option>)}
                    {!vendorCategories.includes('other') && <option value="other">other</option>}
                  </select>
                </label>
                <label className="text-xs">Payment terms
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={paymentTerms} onChange={e => setPaymentTerms(e.target.value)}>
                    {['net15','net30','net45','net60','cod','prepaid'].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="text-xs">Currency
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={currency} onChange={e => setCurrency(e.target.value)}>
                    {['CAD','USD','EUR','CNY'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
              </div>
              <div className="flex items-center gap-2">
                <input className="flex-1 rounded-lg border border-neutral-200 px-3 py-2 text-sm"
                  placeholder="Search code or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
                <label className="flex items-center gap-1 text-xs text-neutral-600">
                  <input type="checkbox" checked={excludeImported} onChange={e => setExcludeImported(e.target.checked)} />
                  Only not-imported
                </label>
              </div>
            </div>

            <div className="flex-1 overflow-auto p-4">
              {isLoading ? <div className="text-sm text-neutral-400">Loading…</div>
                : !data?.items.length ? <div className="text-sm text-neutral-400">No suppliers.</div>
                : <table className="w-full text-sm">
                    <thead className="border-b border-neutral-200">
                      <tr>
                        <th className="px-2 py-2"></th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Code</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Name</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Tel</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map(s => (
                        <tr key={s.erp_supplier_code} className="border-b border-neutral-100">
                          <td className="px-2 py-2"><input type="checkbox" checked={selected.has(s.erp_supplier_code)} onChange={() => toggle(s.erp_supplier_code)} /></td>
                          <td className="px-2 py-2 font-mono text-xs">{s.erp_supplier_code}</td>
                          <td className="px-2 py-2">{s.supplier_name}</td>
                          <td className="px-2 py-2 text-xs text-neutral-500">{s.supplier_tel}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
              }
            </div>

            <div className="border-t border-neutral-200 px-4 py-3 flex items-center justify-between">
              <span className="text-xs text-neutral-500">{selected.size} selected · contact_email will be placeholder</span>
              <div className="flex items-center gap-2">
                {error && <span className="text-xs text-red-600">{error}</span>}
                <button onClick={onClose} className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">Cancel</button>
                <button onClick={submit} disabled={!selected.size || submitting}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white disabled:opacity-60 flex items-center gap-1.5">
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Import {selected.size} vendor{selected.size === 1 ? '' : 's'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
