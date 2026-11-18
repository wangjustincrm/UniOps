import { useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SortTh, useSort, sortParams, Checkbox, Input, Select, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { mdmApi, api } from '@/lib/api'
import { PRESET_PAYMENT_TERMS } from '@/lib/paymentTerms'

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

const PAGE_SIZE = 50

export function ErpVendorImportDrawer({ vendorCategories, onClose }: ErpVendorImportDrawerProps) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [excludeImported, setExcludeImported] = useState(true)
  // mdm-api pages /erp/suppliers, so it sorts too (no sort = by code, as before).
  const [sort, onSort] = useSort<'erp_supplier_code' | 'supplier_name' | 'supplier_tel'>(null, () => setPage(1))
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
    queryFn: () => api.get('/vendors', { page: 1, page_size: 2000 }),
    enabled: excludeImported,
  })
  const importedCodes = (existingVendors?.items || [])
    .map((v) => v.erp_id)
    .filter((c): c is string => !!c)

  const params: Record<string, string | number> = { page, page_size: PAGE_SIZE, ...sortParams(sort) }
  if (search) params.search = search
  if (excludeImported && importedCodes.length) params.exclude_codes = importedCodes.join(',')

  const { data, isLoading, error: loadError } = useQuery<{ items: ErpSupplierRow[]; total: number }>({
    queryKey: ['erp-import-suppliers', search, excludeImported, importedCodes.join(','), page, sort],
    queryFn: () => mdmApi.get('/erp/suppliers', params),
  })

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, total)

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
      <div className="bg-surface w-full max-w-3xl h-full overflow-hidden flex flex-col">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">Import vendors from ERP</h2>
          <Button variant="subtle" size="icon-xs" aria-label="Clear" onClick={onClose}><X className="h-4 w-4 text-fg-muted" /></Button>
        </div>

        {result ? (
          <div className="flex-1 overflow-auto p-4 space-y-3">
            <div className="rounded-lg border border-success-line bg-success-soft px-4 py-3 text-sm">
              <strong>{result.created} created</strong>, {result.errors.length} errors.
            </div>
            {result.errors.length > 0 && (
              <ul className="text-xs text-warning-fg list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e.erp_supplier_code}: {e.reason}</li>)}
              </ul>
            )}
            <p className="text-xs text-fg-muted">Vendor contact emails are placeholders (<code>code@erp.local</code>). Update them in the vendor list.</p>
            <button onClick={onClose} className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white">Done</button>
          </div>
        ) : (
          <>
            <div className="border-b border-line px-4 py-3 space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <label className="text-xs">Category
                  <Select selectSize="sm" className="block w-full mt-1"
                    value={category} onChange={e => setCategory(e.target.value)}>
                    {vendorCategories.map(c => <option key={c} value={c}>{c}</option>)}
                    {!vendorCategories.includes('other') && <option value="other">other</option>}
                  </Select>
                </label>
                <label className="text-xs">Payment terms
                  <Select selectSize="sm" className="block w-full mt-1"
                    value={paymentTerms} onChange={e => setPaymentTerms(e.target.value)}>
                    {PRESET_PAYMENT_TERMS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </Select>
                </label>
                <label className="text-xs">Currency
                  <Select selectSize="sm" className="block w-full mt-1"
                    value={currency} onChange={e => setCurrency(e.target.value)}>
                    {['CAD','USD','EUR','CNY'].map(c => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </label>
              </div>
              <div className="flex items-center gap-2">
                <Input className="flex-1"
                  placeholder="Search code or name…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
                <label className="flex items-center gap-1 text-xs text-fg-secondary">
                  <Checkbox checked={excludeImported} onChange={e => { setExcludeImported(e.target.checked); setPage(1) }} />
                  Only not-imported
                </label>
              </div>
            </div>

            <div className="flex-1 overflow-auto p-4">
              {isLoading ? <div className="text-sm text-fg-muted">Loading…</div>
                : loadError ? (
                    <div className="rounded-lg border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
                      Could not load ERP suppliers: {loadError instanceof Error ? loadError.message : String(loadError)}
                    </div>
                  )
                : !data?.items.length ? <div className="text-sm text-fg-muted">No suppliers.</div>
                : <Table density="compact">
                    <THead>
                      <Tr>
                        <Th></Th>
                        <SortTh label="Code" sortKey="erp_supplier_code" sort={sort} onSort={onSort} className="px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted" />
                        <SortTh label="Name" sortKey="supplier_name" sort={sort} onSort={onSort} className="px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted" />
                        <SortTh label="Tel" sortKey="supplier_tel" sort={sort} onSort={onSort} className="px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted" />
                      </Tr>
                    </THead>
                    <TBody>
                      {data.items.map(s => (
                        <Tr key={s.erp_supplier_code}>
                          <Td><Checkbox checked={selected.has(s.erp_supplier_code)} onChange={() => toggle(s.erp_supplier_code)} /></Td>
                          <Td className="font-mono text-xs">{s.erp_supplier_code}</Td>
                          <Td>{s.supplier_name}</Td>
                          <Td className="text-xs text-fg-muted">{s.supplier_tel}</Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
              }
            </div>

            <div className="border-t border-line px-4 py-2 flex items-center justify-between text-xs text-fg-muted">
              <span>{total === 0 ? 'No suppliers' : `Showing ${rangeStart}–${rangeEnd} of ${total.toLocaleString()}`}</span>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="xs"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page <= 1 || isLoading}
                 >Prev</Button>
                <span>Page {page} / {totalPages}</span>
                <Button variant="secondary" size="xs"
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages || isLoading}
                 >Next</Button>
              </div>
            </div>

            <div className="border-t border-line px-4 py-3 flex items-center justify-between">
              <span className="text-xs text-fg-muted">{selected.size} selected · contact_email will be placeholder</span>
              <div className="flex items-center gap-2">
                {error && <span className="text-xs text-danger-fg">{error}</span>}
                <Button variant="secondary" size="md" onClick={onClose}>Cancel</Button>
                <button onClick={submit} disabled={!selected.size || submitting}
                  className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 flex items-center gap-1.5">
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
