import { useState, useRef } from 'react'
import {
  Search, Plus, Pencil, X, Check, Filter,
  Building2, ToggleLeft, ToggleRight, Download, Upload, Database, AlertTriangle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { formatDate } from '@/lib/utils'
import { downloadCsv } from '@/lib/api'
import { normalizeEmailList } from '@/lib/emailList'
import { useVendors, useCreateVendor, useUpdateVendor, useImportVendors } from '@/hooks/useVendors'
import type { VendorImportResult } from '@/hooks/useVendors'
import { vendorService } from '@/services/vendors'
import type { ApiVendor, CreateVendorBody, SimilarVendor, SimilarVendors, UpdateVendorBody } from '@/services/vendors'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import {
  PRESET_PAYMENT_TERMS, MAX_NET_DAYS, customDaysToTerms, formatPaymentTerms, isPresetTerms, netDays,
} from '@/lib/paymentTerms'
import { ErpVendorImportDrawer } from './ErpVendorImportDrawer'
import { FileInput, Modal, Radio, Segmented, Select, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, useSort } from '@uniops/shell'

type VendorSortKey =
  | 'code' | 'erp_id' | 'name' | 'category' | 'contact'
  | 'payment_terms' | 'currency' | 'status'
const VENDOR_TH = 'px-4 py-3 text-left text-xs font-semibold text-fg-muted whitespace-nowrap'

// Select value for "Custom (Net N days)"; the day count lives in customDays.
const CUSTOM_TERMS = '__custom'


// ─── Form types ───────────────────────────────────────────────────────────────

interface VendorFormData {
  code: string
  erpId: string
  name: string
  category: string
  contactName: string
  contactEmail: string
  remittanceEmail: string
  phone: string
  address: string
  paymentTerms: string
  customDays: string
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
  remittanceEmail: '',
  phone: '',
  address: '',
  paymentTerms: 'net30',
  customDays: '',
  maxPrepaymentPct: '',
  currency: 'CAD',
  active: true,
  notes: '',
}

function termsToForm(terms: string): Pick<VendorFormData, 'paymentTerms' | 'customDays'> {
  if (isPresetTerms(terms)) return { paymentTerms: terms, customDays: '' }
  // Non-preset net<N> opens as Custom with the days filled in. Anything else
  // (a legacy code) opens as Custom with the box empty, so saving forces a
  // real choice instead of silently re-sending a code nothing understands.
  const n = netDays(terms)
  return { paymentTerms: CUSTOM_TERMS, customDays: n != null ? String(n) : '' }
}

/** The code to send; only call after validate() passed. */
function formTerms(f: VendorFormData): string {
  if (f.paymentTerms !== CUSTOM_TERMS) return f.paymentTerms
  const r = customDaysToTerms(f.customDays)
  return 'terms' in r ? r.terms : f.paymentTerms
}

function vendorToForm(v: ApiVendor): VendorFormData {
  return {
    code: v.code,
    erpId: v.erp_id ?? '',
    name: v.name,
    category: v.category,
    contactName: v.contact_name,
    contactEmail: v.contact_email,
    remittanceEmail: v.remittance_email ?? '',
    phone: v.phone ?? '',
    address: v.address ?? '',
    ...termsToForm(v.payment_terms),
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
  saveError?: string | null
  saving?: boolean
  /** Likely duplicate (ISS-0268): a warning the user may save through. */
  duplicateWarning?: string | null
  onSaveAnyway?: () => void
}

function VendorForm({ form, onChange, onSave, onCancel, title, errors, categories, currencies, saveError, saving, duplicateWarning, onSaveAnyway }: VendorFormProps) {
  const set = (k: keyof VendorFormData, v: string | boolean) =>
    onChange({ ...form, [k]: v })

  return (
    <Modal
      open
      onClose={onCancel}
      // Closed only by its buttons, as before.
      closeOnEscape={false}
      size="full"
      className="max-w-4xl"
      title={title}
      footer={<>
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={onSave} disabled={saving}>
          <Check className="h-3.5 w-3.5" />
          {saving ? 'Saving…' : 'Save Vendor'}
        </Button>
      </>}
    >

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* POID */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            POID <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.code}
            value={form.code}
            onChange={(e) => set('code', e.target.value.toUpperCase())}
            placeholder="ABC"
            maxLength={50}
          />
          {errors.code && <p className="text-xs text-danger-fg">{errors.code}</p>}
        </div>

        {/* ERP ID */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">ERP ID</label>
          <Input
            value={form.erpId}
            onChange={(e) => set('erpId', e.target.value)}
            placeholder="ERP system reference"
            maxLength={100}
          />
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5 sm:col-span-1 lg:col-span-2">
          <label className="text-xs font-medium text-fg-secondary">
            Vendor Name <span className="text-danger-fg">*</span>
          </label>
          <Input
            error={!!errors.name}
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="Acme Supplies Inc."
          />
          {errors.name && <p className="text-xs text-danger-fg">{errors.name}</p>}
        </div>

        {/* Category */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">
            Category <span className="text-danger-fg">*</span>
          </label>
          <Select
            error={!!errors.category}
            value={form.category}
            onChange={(e) => set('category', e.target.value)}
          >
            <option value="">Select category…</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          {errors.category && <p className="text-xs text-danger-fg">{errors.category}</p>}
        </div>

        {/* Contact Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Contact Name</label>
          <Input
            value={form.contactName}
            onChange={(e) => set('contactName', e.target.value)}
            placeholder="Jane Smith"
          />
        </div>

        {/* Contact Email */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Contact Email</label>
          <Input
            type="text"
            error={!!errors.contactEmail}
            value={form.contactEmail}
            onChange={(e) => set('contactEmail', e.target.value)}
            placeholder="jane@vendor.com, ap@vendor.com"
          />
          {errors.contactEmail && <p className="text-xs text-danger-fg">{errors.contactEmail}</p>}
        </div>

        {/* Remittance Email */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Remittance Email</label>
          <Input
            type="text"
            error={!!errors.remittanceEmail}
            value={form.remittanceEmail}
            onChange={(e) => set('remittanceEmail', e.target.value)}
            placeholder="ap@vendor.com, remit@vendor.com"
          />
          <p className="text-xs text-fg-muted">
            Where remittance advice is sent. Separate several addresses with a comma.
            Defaults to the contact email if left blank — if both are blank, remittance
            advice cannot be sent for this vendor.
          </p>
          {errors.remittanceEmail && <p className="text-xs text-danger-fg">{errors.remittanceEmail}</p>}
        </div>

        {/* Phone */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Phone</label>
          <Input
            value={form.phone}
            onChange={(e) => set('phone', e.target.value)}
            placeholder="(416) 555-0100"
          />
        </div>

        {/* Address */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-fg-secondary">Address</label>
          <Input
            value={form.address}
            onChange={(e) => set('address', e.target.value)}
            placeholder="123 Main St, Toronto, ON M5V 2T6"
          />
        </div>

        {/* Payment Terms */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Payment Terms</label>
          <div className="flex gap-2">
            <Select
              value={form.paymentTerms}
              onChange={(e) => set('paymentTerms', e.target.value)}
            >
              {PRESET_PAYMENT_TERMS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
              <option value={CUSTOM_TERMS}>Custom (Net N days)…</option>
            </Select>
            {form.paymentTerms === CUSTOM_TERMS && (
              <div className="relative w-32 shrink-0">
                <Input
                  type="number"
                  min={1}
                  max={MAX_NET_DAYS}
                  step={1}
                  inputMode="numeric"
                  aria-label="Net days"
                  error={!!errors.customDays} className="pr-11"
                  value={form.customDays}
                  onChange={(e) => set('customDays', e.target.value)}
                  placeholder="21"
                  autoFocus
                />
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-fg-muted">
                  days
                </span>
              </div>
            )}
          </div>
          {errors.customDays ? (
            <p className="text-xs text-danger-fg">{errors.customDays}</p>
          ) : form.paymentTerms === CUSTOM_TERMS && (
            <p className="text-xs text-fg-muted">Due this many days after the invoice date.</p>
          )}
        </div>

        {/* Max Prepayment % — only relevant when payment_terms = prepayment */}
        {form.paymentTerms === 'prepayment' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">
              Max Prepayment % <span className="text-fg-muted font-normal">(1–100, overrides system cap)</span>
            </label>
            <Input
              type="number"
              min={1}
              max={100}
              step={1}
              value={form.maxPrepaymentPct}
              onChange={(e) => set('maxPrepaymentPct', e.target.value)}
              placeholder="e.g. 50"
            />
          </div>
        )}

        {/* Currency */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Currency</label>
          <Select
            value={form.currency}
            onChange={(e) => set('currency', e.target.value)}
          >
            {currencies.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Status</label>
          <div className="flex items-center gap-4 h-10">
            {([true, false] as boolean[]).map((val) => (
              <label key={String(val)} className="flex items-center gap-2 cursor-pointer">
                <Radio
                 
                  checked={form.active === val}
                  onChange={() => set('active', val)}
                 
                />
                <span className="text-sm text-fg-secondary">{val ? 'Active' : 'Inactive'}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Notes */}
        <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
          <label className="text-xs font-medium text-fg-secondary">Notes</label>
          <Input
            value={form.notes}
            onChange={(e) => set('notes', e.target.value)}
            placeholder="Optional notes about this vendor"
          />
        </div>
      </div>

      {saveError && (
        <div className="mt-4 rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          {saveError}
        </div>
      )}

      {duplicateWarning && (
        <div role="alert" className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-sm text-warning-fg">
          <span>{duplicateWarning} Check it is not the same vendor before saving.</span>
          <Button variant="secondary" size="sm" onClick={onSaveAnyway} disabled={saving}>
            Save anyway
          </Button>
        </div>
      )}
    </Modal>
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
  // Paged by the API, so the API sorts (null = its default, by POID).
  const [sort, onSort] = useSort<VendorSortKey>(null, () => setPage(1))

  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [formData, setFormData] = useState<VendorFormData>(BLANK_FORM)
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof VendorFormData, string>>>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  // ISS-0268: a hand-entered vendor with the same name or ERP ID as one we
  // already have (e.g. ALS / ALS-DUP, ERP ID 0001155) is warned about, not
  // refused — VND-001 refuses only a repeated POID.
  const [duplicateWarning, setDuplicateWarning] = useState<string | null>(null)
  const [editOriginal, setEditOriginal] = useState<ApiVendor | null>(null)
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
    active_only: activeTab === 'active' ? true : undefined,
    // Filtered by the API: dropping active rows from one fetched page left the
    // tab near-empty while the pager still counted every vendor.
    inactive_only: activeTab === 'inactive' ? true : undefined,
    ...sortParams(sort),
    page,
    page_size: pageSize,
  })

  const vendors = vendorsData?.items ?? []
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
  // page_size must satisfy the API's ge=5 floor; total is independent of it.
  const { data: allData } = useVendors({ page: 1, page_size: 5 })
  const totalVendors = allData?.total ?? 0

  const filtered = vendors

  // ── Validation ──────────────────────────────────────────────────────────────
  const validate = (f: VendorFormData): boolean => {
    const e: Partial<Record<keyof VendorFormData, string>> = {}
    if (!f.code.trim()) e.code = 'Required'
    if (!f.name.trim()) e.name = 'Required'
    if (!f.category.trim()) e.category = 'Required'
    if (!normalizeEmailList(f.contactEmail).valid) {
      e.contactEmail = 'Invalid email address'
    }
    if (!normalizeEmailList(f.remittanceEmail).valid) {
      e.remittanceEmail = 'Invalid email address'
    }
    if (f.paymentTerms === CUSTOM_TERMS) {
      const r = customDaysToTerms(f.customDays)
      if ('error' in r) e.customDays = r.error
    }
    setFormErrors(e)
    return Object.keys(e).length === 0
  }

  const openAdd = () => {
    setFormData({ ...BLANK_FORM })
    setFormErrors({})
    setSaveError(null)
    setDuplicateWarning(null)
    setEditOriginal(null)
    setMode('add')
  }

  const openEdit = (vendor: ApiVendor) => {
    setFormData(vendorToForm(vendor))
    setFormErrors({})
    setSaveError(null)
    setDuplicateWarning(null)
    setEditOriginal(vendor)
    setMode({ edit: vendor.id })
  }

  const asMessage = (err: unknown) =>
    err instanceof Error ? err.message : 'Failed to save vendor'

  // On edit only a CHANGED name / ERP ID is checked, so a vendor that already
  // has a twin does not warn on every contact-detail edit.
  const findDuplicates = async (): Promise<string | null> => {
    const name = formData.name.trim()
    const erpId = formData.erpId.trim()
    const squash = (v: string) => v.split(/\s+/).join('').toLowerCase()
    const orig = typeof mode === 'object' ? editOriginal : null
    const q = {
      name: !orig || squash(orig.name) !== squash(name) ? name : undefined,
      erp_id: !orig || (orig.erp_id ?? '').trim().toLowerCase() !== erpId.toLowerCase() ? erpId : undefined,
      exclude_id: orig?.id,
    }
    if (!q.name && !q.erp_id) return null
    let found: SimilarVendors
    try {
      found = await vendorService.similar(q)
    } catch {
      return null // a failed check must not stop the save; the warning is advisory
    }
    const codes = (vs: SimilarVendor[]) =>
      vs.map((v) => (v.is_active ? v.code : `${v.code} (inactive)`)).join(', ')
    const byName = codes(found.name_matches)
    const byErp = codes(found.erp_id_matches)
    if (byName && byName === byErp) return `A vendor with this name and ERP ID already exists: ${byName}.`
    return [
      byName && `A vendor with this name already exists: ${byName}.`,
      byErp && `A vendor with this ERP ID already exists: ${byErp}.`,
    ].filter(Boolean).join(' ') || null
  }

  const handleSave = async () => {
    if (!validate(formData)) return
    setSaveError(null)
    setDuplicateWarning(null)
    const warning = await findDuplicates()
    if (warning) { setDuplicateWarning(warning); return }
    saveVendor()
  }

  const saveVendor = () => {
    setDuplicateWarning(null)
    if (mode === 'add') {
      const body: CreateVendorBody = {
        code: formData.code.trim(),
        erp_id: formData.erpId.trim() || undefined,
        name: formData.name.trim(),
        category: formData.category.trim(),
        contact_name: formData.contactName.trim(),
        contact_email: normalizeEmailList(formData.contactEmail).normalized,
        remittance_email: normalizeEmailList(formData.remittanceEmail).normalized,
        phone: formData.phone.trim() || undefined,
        address: formData.address.trim() || undefined,
        payment_terms: formTerms(formData),
        max_prepayment_pct: formData.maxPrepaymentPct ? parseFloat(formData.maxPrepaymentPct) : null,
        currency: formData.currency.trim() || 'CAD',
        notes: formData.notes.trim() || undefined,
        is_active: formData.active,
      }
      createVendor.mutate(body, {
        onSuccess: () => setMode('none'),
        onError: (err) => setSaveError(asMessage(err)),
      })
    } else if (typeof mode === 'object' && 'edit' in mode) {
      const editId = mode.edit
      const body: UpdateVendorBody = {
        code: formData.code.trim(),
        erp_id: formData.erpId.trim() || undefined,
        name: formData.name.trim(),
        category: formData.category.trim(),
        contact_name: formData.contactName.trim(),
        contact_email: normalizeEmailList(formData.contactEmail).normalized,
        remittance_email: normalizeEmailList(formData.remittanceEmail).normalized,
        phone: formData.phone.trim() || undefined,
        address: formData.address.trim() || undefined,
        payment_terms: formTerms(formData),
        max_prepayment_pct: formData.maxPrepaymentPct ? parseFloat(formData.maxPrepaymentPct) : null,
        currency: formData.currency.trim() || 'CAD',
        notes: formData.notes.trim() || undefined,
        is_active: formData.active,
      }
      updateVendor.mutate({ id: editId, body }, {
        onSuccess: () => setMode('none'),
        onError: (err) => setSaveError(asMessage(err)),
      })
    }
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
          <h1 className="text-2xl font-semibold text-fg">Vendors</h1>
          <p className="mt-0.5 text-sm text-fg-muted">
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
              <FileInput
                ref={importInputRef}
               
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
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Total</p>
          <p className="mt-1 text-2xl font-semibold text-fg">{totalVendors}</p>
        </div>
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Showing</p>
          <p className="mt-1 text-2xl font-semibold text-primary-700">{total}</p>
        </div>
        <div className="rounded-xl bg-surface shadow-sm px-4 py-3">
          <p className="text-xs text-fg-muted font-medium">Page</p>
          <p className="mt-1 text-2xl font-semibold text-fg-secondary">{page} / {totalPages}</p>
        </div>
      </div>

      {/* Import result / error */}
      {importResult && (
        <div className="rounded-lg border border-success-line bg-success-soft px-4 py-3 text-sm flex items-start justify-between gap-3">
          <div>
            <p className="font-medium text-success-fg">
              Import complete — {importResult.created} created, {importResult.updated} updated
            </p>
            {importResult.errors.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-warning-fg">
                {importResult.errors.map((e, i) => <li key={i} className="flex items-start gap-1.5"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{e}</li>)}
              </ul>
            )}
          </div>
          <Button aria-label="Dismiss" variant="subtle" size="icon-xs" onClick={() => setImportResult(null)} className="shrink-0">
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}
      {importError && (
        <div className="rounded-lg border border-danger-line bg-danger-soft px-4 py-3 text-sm flex items-start justify-between gap-3">
          <p className="text-danger-fg">{importError}</p>
          <Button aria-label="Dismiss" variant="subtle" size="icon-xs" onClick={() => setImportError(null)} className="shrink-0">
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-52">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
          <Input
            placeholder="Search name, POID, ERP ID, contact…"
            value={search}
            onChange={(e) => setSearchAndReset(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-fg-muted" />
          <Select
            value={catFilter}
            onChange={(e) => setCatFilterAndReset(e.target.value)}
           
          >
            <option value="all">All Categories</option>
            {vendorCategories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
        </div>
        {/* Active/All/Inactive tabs */}
        <Segmented items={TABS.map((tab) => ({ key: tab.key, label: tab.label }))} value={activeTab} onChange={setActiveTabAndReset} />
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
          onChange={(f) => { setFormData(f); setDuplicateWarning(null) }}
          onSave={() => { void handleSave() }}
          onCancel={() => { setMode('none'); setSaveError(null); setDuplicateWarning(null) }}
          categories={vendorCategories}
          currencies={enabledCurrencies}
          saveError={saveError}
          saving={createVendor.isPending || updateVendor.isPending}
          duplicateWarning={duplicateWarning}
          onSaveAnyway={saveVendor}
        />
      )}

      {/* Table */}
      <div className="rounded-xl bg-surface shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                <SortTh label="POID" sortKey="code" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="ERP ID" sortKey="erp_id" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Category" sortKey="category" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Contact" sortKey="contact" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Payment Terms" sortKey="payment_terms" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Currency" sortKey="currency" sort={sort} onSort={onSort} className={VENDOR_TH} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={VENDOR_TH} />
                {canWrite && <Th className={VENDOR_TH} />}
              </Tr>
            </THead>
            <TBody>
              {filtered.length === 0 && (
                <Tr>
                  <Td align="center" colSpan={canWrite ? 9 : 8}>
                    <Building2 className="h-8 w-8 text-fg-faint mx-auto mb-3" />
                    <p className="text-sm font-medium text-fg-muted">No vendors found</p>
                    <p className="text-xs text-fg-muted mt-1">
                      {search || catFilter !== 'all' || activeTab !== 'all'
                        ? 'Try adjusting your filters'
                        : 'Add your first vendor to get started'}
                    </p>
                  </Td>
                </Tr>
              )}
              {filtered.map((vendor, i) => (
                <Tr
                  key={vendor.id}
                  className={cn(
                    'border-b border-line-subtle hover:bg-primary-50/40 transition-colors',
                    i % 2 === 1 ? 'bg-surface-subtle/60' : 'bg-surface',
                    !vendor.is_active && 'opacity-60'
                  )}
                >
                  {/* POID */}
                  <Td>
                    <span className="font-mono text-xs font-semibold text-primary-700 bg-primary-50 px-1.5 py-0.5 rounded">
                      {vendor.code}
                    </span>
                  </Td>

                  {/* ERP ID */}
                  <Td className="font-mono text-xs text-fg-muted">
                    {vendor.erp_id || <span className="text-fg-faint">—</span>}
                  </Td>

                  {/* Name */}
                  <Td className="text-fg font-medium max-w-52">
                    <p className="truncate">{vendor.name}</p>
                    {vendor.notes && (
                      <p className="text-xs text-fg-muted truncate">{vendor.notes}</p>
                    )}
                  </Td>

                  {/* Category */}
                  <Td className="text-fg-muted whitespace-nowrap">
                    {vendor.category}
                  </Td>

                  {/* Contact */}
                  <Td>
                    {vendor.contact_name && (
                      <p className="text-fg-secondary font-medium text-xs">{vendor.contact_name}</p>
                    )}
                    {vendor.contact_email && (
                      <p className="text-xs text-fg-muted truncate max-w-44">
                        {vendor.contact_email}
                      </p>
                    )}
                    {vendor.remittance_email && (
                      <p className="text-xs text-fg-muted truncate max-w-44" title="Remittance email">
                        ↳ {vendor.remittance_email}
                      </p>
                    )}
                  </Td>

                  {/* Payment Terms */}
                  <Td className="text-fg-secondary whitespace-nowrap">
                    {formatPaymentTerms(vendor.payment_terms)}
                  </Td>

                  {/* Currency */}
                  <Td className="font-mono text-xs text-fg-muted">
                    {vendor.currency}
                  </Td>

                  {/* Status */}
                  <Td>
                    <span
                      className={cn(
                        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                        vendor.is_active
                          ? 'bg-success-soft text-success-fg'
                          : 'bg-surface-sunken text-fg-muted'
                      )}
                    >
                      {vendor.is_active ? '● Active' : '○ Inactive'}
                    </span>
                  </Td>

                  {/* Actions */}
                  {canWrite && (
                    <Td>
                      <div className="flex items-center justify-end gap-1">
                        {/* No delete (ISS-0268): vendors have no delete in the PRD
                            (EPMS §3.10, Portal "There is no delete"); deactivate
                            retires a vendor. */}
                        <Button aria-label="Edit vendor" variant="subtle" size="icon-xs"
                          onClick={() => openEdit(vendor)}
                         
                          title="Edit vendor"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="subtle"
                          aria-label={vendor.is_active ? 'Deactivate vendor' : 'Activate vendor'}
                          onClick={() => {
                            updateVendor.mutate({
                              id: vendor.id,
                              body: { is_active: !vendor.is_active },
                            })
                          }}
                          className={cn('text-fg-muted',
                            vendor.is_active
                              ? 'hover:bg-warning-soft hover:text-warning-fg'
                              : 'hover:bg-success-soft hover:text-success-fg'
                          )}
                          title={vendor.is_active ? 'Deactivate vendor' : 'Activate vendor'}
                        >
                          {vendor.is_active ? (
                            <ToggleRight className="h-3.5 w-3.5" />
                          ) : (
                            <ToggleLeft className="h-3.5 w-3.5" />
                          )}
                        </Button>
                      </div>
                    </Td>
                  )}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
        {/* Pagination footer */}
        <div className="border-t border-line bg-surface-subtle px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <span>Rows per page:</span>
            <Select selectSize="sm"
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
             
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </Select>
            <span className="text-fg-muted">
              {total === 0 ? '0' : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`} of {total}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="secondary" size="icon-sm"
              onClick={() => setPage(1)}
              disabled={page === 1}
              className="text-sm"
            >«</Button>
            <Button variant="secondary" size="icon-sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="text-sm"
            >‹</Button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              const p = start + i
              return (
                <Button
                  key={p}
                  size="icon-sm"
                  variant={p === page ? 'primary' : 'secondary'}
                  aria-current={p === page ? 'page' : undefined}
                  onClick={() => setPage(p)}
                >{p}</Button>
              )
            })}
            <Button variant="secondary" size="icon-sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="text-sm"
            >›</Button>
            <Button variant="secondary" size="icon-sm"
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages}
              className="text-sm"
            >»</Button>
          </div>
        </div>
      </div>
      {showErpImport && <ErpVendorImportDrawer vendorCategories={vendorCategories} onClose={() => setShowErpImport(false)} />}
    </div>
  )
}
