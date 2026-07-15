import { useState, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  Search, Plus, Pencil, Trash2, X, Check, Filter,
  Building2, ToggleLeft, ToggleRight, Download, Upload, Database,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { formatDate } from '@/lib/utils'
import { downloadCsv } from '@/lib/api'
import { useVendors, useCreateVendor, useUpdateVendor, useImportVendors } from '@/hooks/useVendors'
import type { VendorImportResult } from '@/hooks/useVendors'
import type { ApiVendor, CreateVendorBody, UpdateVendorBody } from '@/services/vendors'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import { ErpVendorImportDrawer } from './ErpVendorImportDrawer'

const PAYMENT_TERMS_LABELS: Record<ApiVendor['payment_terms'], string> = {
  net15: 'Net 15',
  net30: 'Net 30',
  net60: 'Net 60',
  net90: 'Net 90',
  cod: 'COD',
  prepayment: 'Prepayment',
}

const PAYMENT_TERMS_OPTIONS = Object.entries(PAYMENT_TERMS_LABELS) as [
  ApiVendor['payment_terms'],
  string,
][]


// ─── Form types ───────────────────────────────────────────────────────────────

interface VendorFormData {
  code: string
  erpId: string
  name: string
  category: string
  contactName: string
  contactEmail: string
  phone: string
  address: string
  paymentTerms: ApiVendor['payment_terms']
  maxPrepaymentPct: string
  currency: string
  active: boolean
  notes: string
}

const BLANK_FORM: VendorFormData = {
  code: '',
  erpId: '',
  name: '',
  category: '',
  contactName: '',
  contactEmail: '',
  phone: '',
  address: '',
  paymentTerms: 'net30',
  maxPrepaymentPct: '',
  currency: 'CAD',
  active: true,
  notes: '',
}

function vendorToForm(v: ApiVendor): VendorFormData {
  return {
    code: v.code,
    erpId: v.erp_id ?? '',
    name: v.name,
    category: v.category,
    contactName: v.contact_name,
    contactEmail: v.contact_email,
    phone: v.phone ?? '',
    address: v.address ?? '',
    paymentTerms: v.payment_terms,
    maxPrepaymentPct: v.max_prepayment_pct != null ? String(v.max_prepayment_pct) : '',
    currency: v.currency,
    active: v.is_active,
    notes: v.notes ?? '',
  }
}

// ─── Vendor Form Component ────────────────────────────────────────────────────

interface VendorFormProps {
  form: VendorFormData
  onChange: (f: VendorFormData) => void
  onSave: () => void
  onCancel: () => void
  title: string
  errors: Partial<Record<keyof VendorFormData, string>>
  categories: string[]
  currencies: string[]
}

function VendorForm({ form, onChange, onSave, onCancel, title, errors, categories, currencies }: VendorFormProps) {
  const set = (k: keyof VendorFormData, v: string | boolean) =>
    onChange({ ...form, [k]: v })

  const inputCls = (err?: string) =>
    cn(
      'h-10 w-full rounded-lg border bg-neutral-100 px-3 text-sm focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors',
      err ? 'border-danger-600 bg-danger-50' : 'border-neutral-200'
    )

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4 animate-[overlay-in_0.15s_ease-out]">
      <div className="w-full max-w-4xl max-h-[90vh] overflow-y-auto rounded-xl border border-primary-200 bg-white shadow-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* POID */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            POID <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.code)}
            value={form.code}
            onChange={(e) => set('code', e.target.value.toUpperCase())}
            placeholder="ABC"
            maxLength={50}
          />
          {errors.code && <p className="text-xs text-danger-600">{errors.code}</p>}
        </div>

        {/* ERP ID */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">ERP ID</label>
          <input
            className={inputCls()}
            value={form.erpId}
            onChange={(e) => set('erpId', e.target.value)}
            placeholder="ERP system reference"
            maxLength={100}
          />
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5 sm:col-span-1 lg:col-span-2">
          <label className="text-xs font-medium text-neutral-700">
            Vendor Name <span className="text-danger-600">*</span>
          </label>
          <input
            className={inputCls(errors.name)}
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="Acme Supplies Inc."
          />
          {errors.name && <p className="text-xs text-danger-600">{errors.name}</p>}
        </div>

        {/* Category */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Category <span className="text-danger-600">*</span>
          </label>
          <select
            className={inputCls(errors.category)}
            value={form.category}
            onChange={(e) => set('category', e.target.value)}
          >
            <option value="">Select category…</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          {errors.category && <p className="text-xs text-danger-600">{errors.category}</p>}
        </div>

        {/* Contact Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Contact Name</label>
          <input
            className={inputCls()}
            value={form.contactName}
            onChange={(e) => set('contactName', e.target.value)}
            placeholder="Jane Smith"
          />
        </div>

        {/* Contact Email */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Contact Email</label>
          <input
            type="email"
            className={inputCls(errors.contactEmail)}
            value={form.contactEmail}
            onChange={(e) => set('contactEmail', e.target.value)}
            placeholder="jane@vendor.com"
          />
          {errors.contactEmail && <p className="text-xs text-danger-600">{errors.contactEmail}</p>}
        </div>

        {/* Phone */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Phone</label>
          <input
            className={inputCls()}
            value={form.phone}
            onChange={(e) => set('phone', e.target.value)}
            placeholder="(416) 555-0100"
          />
        </div>

        {/* Address */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-neutral-700">Address</label>
          <input
            className={inputCls()}
            value={form.address}
            onChange={(e) => set('address', e.target.value)}
            placeholder="123 Main St, Toronto, ON M5V 2T6"
          />
        </div>

        {/* Payment Terms */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Payment Terms</label>
          <select
            className={inputCls()}
            value={form.paymentTerms}
            onChange={(e) => set('paymentTerms', e.target.value as ApiVendor['payment_terms'])}
          >
            {PAYMENT_TERMS_OPTIONS.map(([val, label]) => (
              <option key={val} value={val}>
                {label}
              </option>
            ))}
          </select>
        </div>

        {/* Max Prepayment % — only relevant when payment_terms = prepayment */}
        {form.paymentTerms === 'prepayment' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Max Prepayment % <span className="text-neutral-400 font-normal">(1–100, overrides system cap)</span>
            </label>
            <input
              type="number"
              min={1}
              max={100}
              step={1}
              className={inputCls()}
              value={form.maxPrepaymentPct}
              onChange={(e) => set('maxPrepaymentPct', e.target.value)}
              placeholder="e.g. 50"
            />
          </div>
        )}

        {/* Currency */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Currency</label>
          <select
            className={inputCls()}
            value={form.currency}
            onChange={(e) => set('currency', e.target.value)}
          >
            {currencies.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Status</label>
          <div className="flex items-center gap-4 h-10">
            {([true, false] as boolean[]).map((val) => (
              <label key={String(val)} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  checked={form.active === val}
                  onChange={() => set('active', val)}
                  className="accent-primary-600"
                />
                <span className="text-sm text-neutral-700">{val ? 'Active' : 'Inactive'}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Notes */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-neutral-700">Notes</label>
          <input
            className={inputCls()}
            value={form.notes}
            onChange={(e) => set('notes', e.target.value)}
            placeholder="Optional notes about this vendor"
          />
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2 border-t border-primary-200 pt-4">
        <Button variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={onSave}>
          <Check className="h-3.5 w-3.5" />
          Save Vendor
        </Button>
      </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

export default function VendorsPage() {
  const createVendor = useCreateVendor()
  const updateVendor = useUpdateVendor()
  const importVendors = useImportVendors()
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const perms = useRolePermissions().data?.permissions
  const vendorCategories = (config as any)?.vendor_categories ?? []
  const enabledCurrencies = config?.enabled_currencies ?? ['CAD', 'USD', 'EUR', 'RMB']
  const importInputRef = useRef<HTMLInputElement>(null)

  const canWrite = user?.role === 'system_admin' || !!perms?.vendor_master

  // ── Filters & pagination state ───────────────────────────────────────────────
  const [search, setSearch] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const [activeTab, setActiveTab] = useState<'all' | 'active' | 'inactive'>('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [formData, setFormData] = useState<VendorFormData>(BLANK_FORM)
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof VendorFormData, string>>>({})
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<VendorImportResult | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [showErpImport, setShowErpImport] = useState(false)

  // Reset to page 1 when filters change
  const setSearchAndReset = (v: string) => { setSearch(v); setPage(1) }
  const setCatFilterAndReset = (v: string) => { setCatFilter(v); setPage(1) }
  const setActiveTabAndReset = (v: typeof activeTab) => { setActiveTab(v); setPage(1) }

  const { data: vendorsData } = useVendors({
    search: search || undefined,
    category: catFilter === 'all' ? undefined : catFilter,
    active_only: activeTab === 'active' ? true : activeTab === 'inactive' ? undefined : undefined,
    page,
    page_size: pageSize,
  })

  // For inactive tab we filter client-side since the API doesn't support it directly
  const rawItems = vendorsData?.items ?? []
  const vendors = activeTab === 'inactive' ? rawItems.filter((v) => !v.is_active) : rawItems
  const total = vendorsData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const handleExport = () => downloadCsv('/vendors/export', undefined, 'vendors.csv')

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setImportResult(null)
    setImportError(null)
    importVendors.mutate(file, {
      onSuccess: (result) => setImportResult(result),
      onError: (err) => setImportError(err instanceof Error ? err.message : 'Import failed'),
    })
  }

  // ── Stats (from full list via separate unfilitered query) ───────────────────
  const { data: allData } = useVendors({ page: 1, page_size: 1 })
  const totalVendors = allData?.total ?? 0

  const filtered = vendors

  // ── Validation ──────────────────────────────────────────────────────────────
  const validate = (f: VendorFormData): boolean => {
    const e: Partial<Record<keyof VendorFormData, string>> = {}
    if (!f.code.trim()) e.code = 'Required'
    if (!f.name.trim()) e.name = 'Required'
    if (!f.category.trim()) e.category = 'Required'
    if (f.contactEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.contactEmail)) {
      e.contactEmail = 'Invalid email address'
    }
    setFormErrors(e)
    return Object.keys(e).length === 0
  }

  const openAdd = () => {
    setFormData({ ...BLANK_FORM })
    setFormErrors({})
    setMode('add')
    setDeleteConfirm(null)
  }

  const openEdit = (vendor: ApiVendor) => {
    setFormData(vendorToForm(vendor))
    setFormErrors({})
    setMode({ edit: vendor.id })
    setDeleteConfirm(null)
  }

  const handleSave = () => {
    if (!validate(formData)) return
    if (mode === 'add') {
      const body: CreateVendorBody = {
        code: formData.code.trim(),
        erp_id: formData.erpId.trim() || undefined,
        name: formData.name.trim(),
        category: formData.category.trim(),
        contact_name: formData.contactName.trim(),
        contact_email: formData.contactEmail.trim(),
        phone: formData.phone.trim() || undefined,
        address: formData.address.trim() || undefined,
        payment_terms: formData.paymentTerms,
        max_prepayment_pct: formData.maxPrepaymentPct ? parseFloat(formData.maxPrepaymentPct) : null,
        currency: formData.currency.trim() || 'CAD',
        notes: formData.notes.trim() || undefined,
        is_active: formData.active,
      }
      createVendor.mutate(body)
    } else if (typeof mode === 'object' && 'edit' in mode) {
      const body: UpdateVendorBody = {
        code: formData.code.trim(),
        erp_id: formData.erpId.trim() || undefined,
        name: formData.name.trim(),
        category: formData.category.trim(),
        contact_name: formData.contactName.trim(),
        contact_email: formData.contactEmail.trim(),
        phone: formData.phone.trim() || undefined,
        address: formData.address.trim() || undefined,
        payment_terms: formData.paymentTerms,
        max_prepayment_pct: formData.maxPrepaymentPct ? parseFloat(formData.maxPrepaymentPct) : null,
        currency: formData.currency.trim() || 'CAD',
        notes: formData.notes.trim() || undefined,
        is_active: formData.active,
      }
      updateVendor.mutate({ id: mode.edit, body })
    }
    setMode('none')
  }

  const TABS: { key: typeof activeTab; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'active', label: 'Active' },
    { key: 'inactive', label: 'Inactive' },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Vendors</h1>
          <p className="mt-0.5 text-sm text-neutral-500">
            Manage your supplier and vendor directory
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={handleExport}>
            <Download className="h-4 w-4" />
            Export CSV
          </Button>
          {canWrite && (
            <>
              <Button
                variant="secondary"
                onClick={() => importInputRef.current?.click()}
                disabled={importVendors.isPending}
              >
                <Upload className="h-4 w-4" />
                {importVendors.isPending ? 'Importing…' : 'Import CSV'}
              </Button>
              <input
                ref={importInputRef}
                type="file"
                accept=".csv"
                className="sr-only"
                onChange={handleImportFile}
              />
              <Button variant="secondary" onClick={() => setShowErpImport(true)} disabled={mode !== 'none'}>
                <Database className="h-4 w-4" />
                From ERP
              </Button>
              <Button onClick={openAdd} disabled={mode !== 'none'}>
                <Plus className="h-4 w-4" />
                Add Vendor
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Total</p>
          <p className="mt-1 text-2xl font-bold text-neutral-900">{totalVendors}</p>
        </div>
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Showing</p>
          <p className="mt-1 text-2xl font-bold text-primary-700">{total}</p>
        </div>
        <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-4 py-3">
          <p className="text-xs text-neutral-500 font-medium uppercase tracking-wide">Page</p>
          <p className="mt-1 text-2xl font-bold text-neutral-700">{page} / {totalPages}</p>
        </div>
      </div>

      {/* Import result / error */}
      {importResult && (
        <div className="rounded-lg border border-success-200 bg-success-50 px-4 py-3 text-sm flex items-start justify-between gap-3">
          <div>
            <p className="font-medium text-success-700">
              Import complete — {importResult.created} created, {importResult.updated} updated
            </p>
            {importResult.errors.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-warning-700">
                {importResult.errors.map((e, i) => <li key={i}>⚠ {e}</li>)}
              </ul>
            )}
          </div>
          <button onClick={() => setImportResult(null)} className="text-neutral-400 hover:text-neutral-600 shrink-0">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {importError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm flex items-start justify-between gap-3">
          <p className="text-danger-700">{importError}</p>
          <button onClick={() => setImportError(null)} className="text-neutral-400 hover:text-neutral-600 shrink-0">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-52">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <Input
            placeholder="Search name, POID, ERP ID, contact…"
            value={search}
            onChange={(e) => setSearchAndReset(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-neutral-400" />
          <select
            value={catFilter}
            onChange={(e) => setCatFilterAndReset(e.target.value)}
            className="h-10 rounded-lg border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Categories</option>
            {vendorCategories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        {/* Active/All/Inactive tabs */}
        <div className="flex rounded-lg border border-neutral-200 overflow-hidden">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTabAndReset(tab.key)}
              className={cn(
                'px-3.5 py-2 text-xs font-medium transition-colors',
                activeTab === tab.key
                  ? 'bg-primary-600 text-white'
                  : 'bg-white text-neutral-600 hover:bg-neutral-50'
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Add / Edit form */}
      {mode !== 'none' && canWrite && (
        <VendorForm
          title={
            mode === 'add'
              ? 'Add New Vendor'
              : `Edit — ${formData.name || formData.code}`
          }
          form={formData}
          errors={formErrors}
          onChange={setFormData}
          onSave={handleSave}
          onCancel={() => setMode('none')}
          categories={vendorCategories}
          currencies={enabledCurrencies}
        />
      )}

      {/* Table */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                {[
                  'POID',
                  'ERP ID',
                  'Name',
                  'Category',
                  'Contact',
                  'Payment Terms',
                  'Currency',
                  'Status',
                  ...(canWrite ? [''] : []),
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={canWrite ? 9 : 8} className="py-16 text-center">
                    <Building2 className="h-8 w-8 text-neutral-300 mx-auto mb-3" />
                    <p className="text-sm font-medium text-neutral-500">No vendors found</p>
                    <p className="text-xs text-neutral-400 mt-1">
                      {search || catFilter !== 'all' || activeTab !== 'all'
                        ? 'Try adjusting your filters'
                        : 'Add your first vendor to get started'}
                    </p>
                  </td>
                </tr>
              )}
              {filtered.map((vendor, i) => (
                <tr
                  key={vendor.id}
                  className={cn(
                    'border-b border-neutral-100 hover:bg-primary-50/40 transition-colors',
                    i % 2 === 1 ? 'bg-neutral-50/60' : 'bg-white',
                    !vendor.is_active && 'opacity-60'
                  )}
                >
                  {/* POID */}
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs font-semibold text-primary-700 bg-primary-50 px-1.5 py-0.5 rounded">
                      {vendor.code}
                    </span>
                  </td>

                  {/* ERP ID */}
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">
                    {vendor.erp_id || <span className="text-neutral-300">—</span>}
                  </td>

                  {/* Name */}
                  <td className="px-4 py-3 text-neutral-900 font-medium max-w-52">
                    <p className="truncate">{vendor.name}</p>
                    {vendor.notes && (
                      <p className="text-xs text-neutral-400 truncate">{vendor.notes}</p>
                    )}
                  </td>

                  {/* Category */}
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">
                    {vendor.category}
                  </td>

                  {/* Contact */}
                  <td className="px-4 py-3">
                    {vendor.contact_name && (
                      <p className="text-neutral-700 font-medium text-xs">{vendor.contact_name}</p>
                    )}
                    {vendor.contact_email && (
                      <p className="text-xs text-neutral-400 truncate max-w-44">
                        {vendor.contact_email}
                      </p>
                    )}
                  </td>

                  {/* Payment Terms */}
                  <td className="px-4 py-3 text-neutral-600 whitespace-nowrap">
                    {PAYMENT_TERMS_LABELS[vendor.payment_terms]}
                  </td>

                  {/* Currency */}
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">
                    {vendor.currency}
                  </td>

                  {/* Status */}
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                        vendor.is_active
                          ? 'bg-success-50 text-success-700'
                          : 'bg-neutral-100 text-neutral-500'
                      )}
                    >
                      {vendor.is_active ? '● Active' : '○ Inactive'}
                    </span>
                  </td>

                  {/* Actions */}
                  {canWrite && (
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {deleteConfirm === vendor.id ? (
                          <>
                            <button
                              onClick={() => {
                                // No delete endpoint available — dismiss confirmation
                                setDeleteConfirm(null)
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded text-danger-600 hover:bg-danger-50"
                              title="Confirm delete"
                            >
                              <Check className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => setDeleteConfirm(null)}
                              className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"
                              title="Cancel"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => {
                                openEdit(vendor)
                                setDeleteConfirm(null)
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"
                              title="Edit vendor"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => {
                                updateVendor.mutate({
                                  id: vendor.id,
                                  body: { is_active: !vendor.is_active },
                                })
                              }}
                              className={cn(
                                'flex h-7 w-7 items-center justify-center rounded text-neutral-400',
                                vendor.is_active
                                  ? 'hover:bg-warning-50 hover:text-warning-600'
                                  : 'hover:bg-success-50 hover:text-success-600'
                              )}
                              title={vendor.is_active ? 'Deactivate vendor' : 'Activate vendor'}
                            >
                              {vendor.is_active ? (
                                <ToggleRight className="h-3.5 w-3.5" />
                              ) : (
                                <ToggleLeft className="h-3.5 w-3.5" />
                              )}
                            </button>
                            <button
                              onClick={() => {
                                setDeleteConfirm(vendor.id)
                                setMode('none')
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-danger-50 hover:text-danger-500"
                              title="Delete vendor"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* Pagination footer */}
        <div className="border-t border-neutral-200 bg-neutral-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-neutral-500">
            <span>Rows per page:</span>
            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="h-8 rounded-md border border-neutral-200 bg-white px-2 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <span className="text-neutral-400">
              {total === 0 ? '0' : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`} of {total}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(1)}
              disabled={page === 1}
              className="h-8 w-8 rounded-md border border-neutral-200 bg-white text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >«</button>
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="h-8 w-8 rounded-md border border-neutral-200 bg-white text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >‹</button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              const p = start + i
              return (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={cn(
                    'h-8 w-8 rounded-md border text-sm transition-colors',
                    p === page
                      ? 'border-primary-600 bg-primary-600 text-white'
                      : 'border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50'
                  )}
                >{p}</button>
              )
            })}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="h-8 w-8 rounded-md border border-neutral-200 bg-white text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >›</button>
            <button
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages}
              className="h-8 w-8 rounded-md border border-neutral-200 bg-white text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >»</button>
          </div>
        </div>
      </div>
      {showErpImport && <ErpVendorImportDrawer vendorCategories={vendorCategories} onClose={() => setShowErpImport(false)} />}
    </div>
  )
}
