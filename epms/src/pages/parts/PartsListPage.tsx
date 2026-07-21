import { useState, useRef } from 'react'
import {
  Search, Plus, Pencil, Trash2, X, Check,
  Upload, Download, Filter, AlertCircle, Package, ImagePlus,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatCAD } from '@/lib/utils'
import { cn } from '@/lib/utils'
import {
  useParts, usePartCategories, useCreatePart, useUpdatePart, useDeletePart,
  useExportParts, useImportParts,
} from '@/hooks/useParts'
import type { ApiPart, CreatePartBody, UpdatePartBody } from '@/services/parts'
import { useUomCodes } from '@/hooks/useUoms'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'

// ─── Part form ────────────────────────────────────────────────────────────────

interface PartFormData {
  code: string
  category: string
  name: string
  description: string
  supplier: string
  supplierPartNo: string
  supplierItemId: string
  unitPrice: string
  unit: string
  isActive: boolean
  imageDataUrl: string | null
}

const BLANK_FORM = (suggestedCode: string): PartFormData => ({
  code: suggestedCode,
  category: '',
  name: '',
  description: '',
  supplier: '',
  supplierPartNo: '',
  supplierItemId: '',
  unitPrice: '',
  unit: 'pcs',
  isActive: true,
  imageDataUrl: null,
})

function partToForm(p: ApiPart): PartFormData {
  return {
    code: p.code,
    category: p.category,
    name: p.name,
    description: p.description ?? '',
    supplier: p.supplier,
    supplierPartNo: p.supplier_part_no,
    supplierItemId: p.supplier_item_id ?? '',
    unitPrice: String(p.unit_price),
    unit: p.unit,
    isActive: p.is_active,
    imageDataUrl: p.image_data_url ?? null,
  }
}

// ─── Image upload zone ────────────────────────────────────────────────────────

interface ImageUploadProps {
  value: string | null
  onChange: (dataUrl: string | null) => void
}

function ImageUpload({ value, onChange }: ImageUploadProps) {
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const loadFile = (file: File) => {
    if (!file.type.startsWith('image/')) return
    const reader = new FileReader()
    reader.onload = (e) => onChange(e.target?.result as string)
    reader.readAsDataURL(file)
  }

  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs font-medium text-neutral-700">
        Part Image <span className="text-neutral-400 font-normal">(optional)</span>
      </label>

      {value ? (
        /* Preview */
        <div className="flex items-center gap-3">
          <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-xl border border-neutral-200 bg-neutral-50">
            <img src={value} alt="Part" className="h-full w-full object-contain p-1" />
          </div>
          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-xs text-neutral-600 hover:bg-neutral-50"
            >
              <ImagePlus className="h-3.5 w-3.5" />
              Replace
            </button>
            <button
              type="button"
              onClick={() => onChange(null)}
              className="flex items-center gap-1.5 rounded-lg border border-danger-200 bg-danger-50 px-3 py-1.5 text-xs text-danger-600 hover:bg-danger-100"
            >
              <X className="h-3.5 w-3.5" />
              Remove
            </button>
          </div>
        </div>
      ) : (
        /* Upload zone */
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const file = e.dataTransfer.files?.[0]
            if (file) loadFile(file)
          }}
          className={cn(
            'flex h-20 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed transition-colors',
            dragging
              ? 'border-primary-400 bg-primary-50'
              : 'border-neutral-200 bg-neutral-50 hover:border-primary-300 hover:bg-primary-50/40'
          )}
        >
          <ImagePlus className={cn('h-5 w-5', dragging ? 'text-primary-500' : 'text-neutral-300')} />
          <span className="text-[10px] text-neutral-400">Click or drag image here</span>
        </div>
      )}

      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) loadFile(file)
          e.target.value = ''
        }}
      />
    </div>
  )
}

// ─── Part form ────────────────────────────────────────────────────────────────

interface PartFormProps {
  form: PartFormData
  categories: string[]
  onChanges: (f: PartFormData) => void
  onSave: () => void
  onCancel: () => void
  title: string
  errors: Partial<Record<keyof PartFormData, string>>
}

function PartForm({ form, categories, onChanges, onSave, onCancel, title, errors }: PartFormProps) {
  const set = (k: keyof PartFormData, v: string | boolean | null) =>
    onChanges({ ...form, [k]: v })

  const uomCodes = useUomCodes()
  const unitOptions = form.unit && !uomCodes.includes(form.unit)
    ? [form.unit, ...uomCodes]
    : uomCodes

  const inputCls = (err?: string) =>
    cn(
      'h-10 w-full rounded-lg border bg-neutral-100 px-3 text-sm focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)] transition-colors',
      err ? 'border-danger-600 bg-danger-50' : 'border-neutral-200'
    )

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50/60 p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        <button onClick={onCancel} className="text-neutral-400 hover:text-neutral-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex gap-5">
        {/* Image upload — left side */}
        <div className="shrink-0 w-48">
          <ImageUpload
            value={form.imageDataUrl}
            onChange={(v) => set('imageDataUrl', v)}
          />
        </div>

        {/* Fields — right side */}
        <div className="flex-1 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {/* Code */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Part Code <span className="text-danger-600">*</span>
            </label>
            <input className={inputCls(errors.code)} value={form.code}
              onChange={(e) => set('code', e.target.value)} placeholder="PART-0001" />
            {errors.code && <p className="text-xs text-danger-600">{errors.code}</p>}
          </div>

          {/* Category */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Category <span className="text-danger-600">*</span>
            </label>
            <input
              className={inputCls(errors.category)} list="cat-list"
              value={form.category} onChange={(e) => set('category', e.target.value)}
              placeholder="Bearings, Seals & Gaskets…"
            />
            <datalist id="cat-list">
              {categories.map((c) => <option key={c} value={c} />)}
            </datalist>
            {errors.category && <p className="text-xs text-danger-600">{errors.category}</p>}
          </div>

          {/* Name */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Part Name <span className="text-danger-600">*</span>
            </label>
            <input className={inputCls(errors.name)} value={form.name}
              onChange={(e) => set('name', e.target.value)} placeholder="Ball Bearing 6205-2RS" />
            {errors.name && <p className="text-xs text-danger-600">{errors.name}</p>}
          </div>

          {/* Description */}
          <div className="flex flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
            <label className="text-xs font-medium text-neutral-700">Description</label>
            <input className={inputCls()} value={form.description}
              onChange={(e) => set('description', e.target.value)}
              placeholder="Optional technical description" />
          </div>

          {/* Supplier */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Supplier <span className="text-danger-600">*</span>
            </label>
            <input className={inputCls(errors.supplier)} value={form.supplier}
              onChange={(e) => set('supplier', e.target.value)} placeholder="SKF Canada" />
            {errors.supplier && <p className="text-xs text-danger-600">{errors.supplier}</p>}
          </div>

          {/* Supplier Part No */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Supplier Part No.</label>
            <input className={inputCls()} value={form.supplierPartNo}
              onChange={(e) => set('supplierPartNo', e.target.value)}
              placeholder="6205-2RS/C3" />
          </div>

          {/* Supplier Item ID */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Supplier Item ID</label>
            <input className={inputCls()} value={form.supplierItemId}
              onChange={(e) => set('supplierItemId', e.target.value)}
              placeholder="e.g. Amazon ASIN: B00004RFRN" />
            <p className="text-[10px] text-neutral-400">ASIN, McMaster #, Grainger ID, etc.</p>
          </div>

          {/* Unit Price */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              Unit Price (CAD) <span className="text-danger-600">*</span>
            </label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-neutral-400">$</span>
              <input
                type="number" min="0" step="0.01"
                className={cn(inputCls(errors.unitPrice), 'pl-6')}
                value={form.unitPrice}
                onChange={(e) => set('unitPrice', e.target.value)}
                placeholder="0.00"
              />
            </div>
            {errors.unitPrice && <p className="text-xs text-danger-600">{errors.unitPrice}</p>}
          </div>

          {/* Unit */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Unit (UOM)</label>
            <select className={inputCls()} value={form.unit}
              onChange={(e) => set('unit', e.target.value)}>
              {unitOptions.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>

          {/* Status */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Status</label>
            <div className="flex items-center gap-4 h-10">
              {[true, false].map((val) => (
                <label key={String(val)} className="flex items-center gap-2 cursor-pointer">
                  <input type="radio" checked={form.isActive === val}
                    onChange={() => set('isActive', val)}
                    className="accent-primary-600" />
                  <span className="text-sm text-neutral-700">{val ? 'Active' : 'Inactive'}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2 border-t border-primary-200 pt-4">
        <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" onClick={onSave}>
          <Check className="h-3.5 w-3.5" />
          Save Part
        </Button>
      </div>
    </div>
  )
}

// ─── Part image thumbnail (reusable) ─────────────────────────────────────────

export function PartThumbnail({ src, size = 36 }: { src?: string | null; size?: number }) {
  if (src) {
    return (
      <div
        className="shrink-0 overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50"
        style={{ width: size, height: size }}
      >
        <img src={src} alt="" className="h-full w-full object-contain p-0.5" />
      </div>
    )
  }
  return (
    <div
      className="shrink-0 flex items-center justify-center rounded-lg bg-neutral-100"
      style={{ width: size, height: size }}
    >
      <Package className="text-neutral-300" style={{ width: size * 0.5, height: size * 0.5 }} />
    </div>
  )
}

// ─── CSV Import Toast ──────────────────────────────────────────────────────────

interface ImportResult { imported: number; errors: number }

// ─── Main page ────────────────────────────────────────────────────────────────

export default function PartsListPage() {
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const canWrite = user?.role === 'system_admin' || !!perms?.parts_catalog

  const [search, setSearch] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [mode, setMode] = useState<'none' | 'add' | { edit: string }>('none')
  const [formData, setFormData] = useState<PartFormData>(BLANK_FORM(''))
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof PartFormData, string>>>({})
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [importError, setImportError] = useState<string | null>(null)

  const csvImportRef = useRef<HTMLInputElement>(null)

  // Reset to page 1 when filters change
  const setSearchAndReset = (v: string) => { setSearch(v); setPage(1) }
  const setCatAndReset = (v: string) => { setCatFilter(v); setPage(1) }

  const { data: partsData } = useParts({
    search: search || undefined,
    category: catFilter === 'all' ? undefined : catFilter,
    page,
    page_size: pageSize,
  })
  const parts = partsData?.items ?? []
  const total = partsData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const filtered = parts

  // Total count (unfiltered)
  const { data: allData } = useParts({ page: 1, page_size: 1 })
  const totalAll = allData?.total ?? 0

  // Category list from dedicated endpoint
  const { data: categories = [] } = usePartCategories()

  const createPart = useCreatePart()
  const updatePart = useUpdatePart()
  const deletePart = useDeletePart()
  const exportParts = useExportParts()
  const importParts = useImportParts()

  const suggestedCode = `P-${(totalAll + 1).toString().padStart(4, '0')}`

  // ── Form validation ────────────────────────────────────────────
  const validate = (f: PartFormData): boolean => {
    const e: typeof formErrors = {}
    if (!f.code.trim()) e.code = 'Required'
    if (!f.category.trim()) e.category = 'Required'
    if (!f.name.trim()) e.name = 'Required'
    if (!f.supplier.trim()) e.supplier = 'Required'
    const price = parseFloat(f.unitPrice)
    if (isNaN(price) || price < 0) e.unitPrice = 'Must be a valid number ≥ 0'
    setFormErrors(e)
    return Object.keys(e).length === 0
  }

  const openAdd = () => {
    setFormData(BLANK_FORM(suggestedCode))
    setFormErrors({})
    setMode('add')
    setDeleteConfirm(null)
  }

  const openEdit = (part: ApiPart) => {
    setFormData(partToForm(part))
    setFormErrors({})
    setMode({ edit: part.id })
    setDeleteConfirm(null)
  }

  const handleSave = () => {
    if (!validate(formData)) return
    if (mode === 'add') {
      const body: CreatePartBody = {
        code: formData.code.trim(),
        category: formData.category.trim(),
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        supplier: formData.supplier.trim(),
        supplier_part_no: formData.supplierPartNo.trim(),
        supplier_item_id: formData.supplierItemId.trim() || undefined,
        unit_price: parseFloat(formData.unitPrice) || 0,
        unit: formData.unit,
        is_active: formData.isActive,
        image_data_url: formData.imageDataUrl,
      }
      createPart.mutate(body)
    } else if (typeof mode === 'object' && 'edit' in mode) {
      const body: UpdatePartBody = {
        code: formData.code.trim(),
        category: formData.category.trim(),
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        supplier: formData.supplier.trim(),
        supplier_part_no: formData.supplierPartNo.trim(),
        supplier_item_id: formData.supplierItemId.trim() || undefined,
        unit_price: parseFloat(formData.unitPrice) || 0,
        unit: formData.unit,
        is_active: formData.isActive,
        image_data_url: formData.imageDataUrl,
      }
      updatePart.mutate({ id: mode.edit, body })
    }
    setMode('none')
  }

  // ── CSV Import ─────────────────────────────────────────────────
  const handleCsvFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    importParts.mutate(file, {
      onSuccess: (result) => {
        setImportError(null)
        setImportResult(result)
        setTimeout(() => setImportResult(null), 5000)
      },
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Import failed'
        setImportError(msg)
        setTimeout(() => setImportError(null), 8000)
      },
    })
    e.target.value = ''
  }

  // ── CSV Export ─────────────────────────────────────────────────
  const handleExport = () => {
    exportParts.mutate()
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Parts Catalog</h1>
          <p className="mt-0.5 text-sm text-neutral-500">
            {totalAll} total parts
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button variant="secondary" size="sm" onClick={handleExport}>
            <Download className="h-4 w-4" />
            Export CSV
          </Button>
          {canWrite && (
            <>
              <Button variant="secondary" size="sm" onClick={() => csvImportRef.current?.click()}>
                <Upload className="h-4 w-4" />
                Import CSV
              </Button>
              <input ref={csvImportRef} type="file" accept=".csv,text/csv" className="hidden"
                onChange={handleCsvFile} />
              <Button onClick={openAdd} disabled={mode !== 'none'}>
                <Plus className="h-4 w-4" />
                Add Part
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Import error toast */}
      {importError && (
        <div className="flex items-center gap-3 rounded-xl px-4 py-3 text-sm bg-danger-50 border border-danger-200 text-danger-700">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>Import failed: {importError}</span>
          <button onClick={() => setImportError(null)} className="ml-auto">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Import result toast */}
      {importResult && (
        <div className={cn(
          'flex items-center gap-3 rounded-xl px-4 py-3 text-sm',
          importResult.errors > 0
            ? 'bg-warning-50 border border-warning-200 text-warning-700'
            : 'bg-success-50 border border-success-200 text-success-700'
        )}>
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>
            Imported <strong>{importResult.imported}</strong> parts successfully.
            {importResult.errors > 0 && ` ${importResult.errors} rows skipped (missing required fields).`}
          </span>
          <button onClick={() => setImportResult(null)} className="ml-auto">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* CSV format hint */}
      <div className="rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-2.5 text-xs text-neutral-500">
        <span className="font-medium text-neutral-600">CSV format: </span>
        code, category, name, description, supplier, supplierPartNo, supplierItemId, unitPrice, unit
        <span className="ml-2 text-neutral-400">(images managed via the form only)</span>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-52">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <Input placeholder="Search code, name, supplier…"
            value={search} onChange={(e) => setSearchAndReset(e.target.value)} className="pl-9" />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-neutral-400" />
          <select
            value={catFilter}
            onChange={(e) => setCatAndReset(e.target.value)}
            className="h-10 rounded-lg border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Categories</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {/* Add / Edit form */}
      {mode !== 'none' && (
        <PartForm
          title={mode === 'add' ? 'Add New Part' : `Edit — ${formData.name || formData.code}`}
          form={formData}
          categories={categories}
          errors={formErrors}
          onChanges={setFormData}
          onSave={handleSave}
          onCancel={() => setMode('none')}
        />
      )}

      {/* Table */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="w-12 px-3 py-3" />
                {[
                  'Code', 'Category', 'Name', 'Supplier',
                  'Supplier Part No.', 'Unit Price', 'UOM', 'Status', '',
                ].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-16 text-center">
                    <Package className="h-8 w-8 text-neutral-300 mx-auto mb-3" />
                    <p className="text-sm font-medium text-neutral-500">No parts found</p>
                    <p className="text-xs text-neutral-400 mt-1">
                      {search || catFilter !== 'all' ? 'Try adjusting your filters' : 'Add your first part to the catalog'}
                    </p>
                  </td>
                </tr>
              )}
              {filtered.map((part, i) => (
                <tr
                  key={part.id}
                  className={cn(
                    'border-b border-neutral-100 hover:bg-primary-50/40 transition-colors',
                    i % 2 === 1 ? 'bg-neutral-50/60' : 'bg-white',
                    !part.is_active && 'opacity-60'
                  )}
                >
                  {/* Thumbnail */}
                  <td className="px-3 py-2.5">
                    <PartThumbnail src={part.image_data_url} size={36} />
                  </td>
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs font-semibold text-primary-700 bg-primary-50 px-1.5 py-0.5 rounded">
                      {part.code}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">{part.category}</td>
                  <td className="px-4 py-3 text-neutral-900 font-medium max-w-52">
                    <p className="truncate">{part.name}</p>
                    {part.description && (
                      <p className="text-xs text-neutral-400 truncate">{part.description}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-neutral-600 whitespace-nowrap">{part.supplier}</td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">{part.supplier_part_no || '—'}</td>
                  <td className="px-4 py-3 font-mono text-sm text-neutral-900 text-right">
                    {formatCAD(part.unit_price)}
                  </td>
                  <td className="px-4 py-3 text-neutral-500">{part.unit}</td>
                  <td className="px-4 py-3">
                    <span className={cn(
                      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                      part.is_active ? 'bg-success-50 text-success-700' : 'bg-neutral-100 text-neutral-500'
                    )}>
                      {part.is_active ? '● Active' : '○ Inactive'}
                    </span>
                  </td>
                  {canWrite && (
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {deleteConfirm === part.id ? (
                        <>
                          <button onClick={() => { deletePart.mutate(part.id); setDeleteConfirm(null) }}
                            className="flex h-7 w-7 items-center justify-center rounded text-danger-600 hover:bg-danger-50"
                            title="Confirm delete">
                            <Check className="h-3.5 w-3.5" />
                          </button>
                          <button onClick={() => setDeleteConfirm(null)}
                            className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-neutral-100"
                            title="Cancel">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => { openEdit(part); setDeleteConfirm(null) }}
                            className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-primary-50 hover:text-primary-600"
                            title="Edit part">
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button onClick={() => { setDeleteConfirm(part.id); setMode('none') }}
                            className="flex h-7 w-7 items-center justify-center rounded text-neutral-400 hover:bg-danger-50 hover:text-danger-500"
                            title="Delete part">
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
        <div className="flex items-center justify-between border-t border-neutral-200 bg-neutral-50 px-4 py-2.5 text-xs text-neutral-500 gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span>Rows per page:</span>
            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="h-7 rounded border border-neutral-200 bg-white px-2 text-xs"
            >
              {[10, 20, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span>
              {total === 0 ? '0' : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`} of {total}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(1)} disabled={page === 1}
              className="flex h-7 w-7 items-center justify-center rounded hover:bg-neutral-200 disabled:opacity-40">«</button>
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}
              className="flex h-7 w-7 items-center justify-center rounded hover:bg-neutral-200 disabled:opacity-40">‹</button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              const p = start + i
              return (
                <button key={p} onClick={() => setPage(p)}
                  className={cn('flex h-7 w-7 items-center justify-center rounded text-xs',
                    p === page ? 'bg-primary-600 text-white font-semibold' : 'hover:bg-neutral-200'
                  )}>
                  {p}
                </button>
              )
            })}
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}
              className="flex h-7 w-7 items-center justify-center rounded hover:bg-neutral-200 disabled:opacity-40">›</button>
            <button onClick={() => setPage(totalPages)} disabled={page === totalPages}
              className="flex h-7 w-7 items-center justify-center rounded hover:bg-neutral-200 disabled:opacity-40">»</button>
          </div>
        </div>
      </div>
    </div>
  )
}
