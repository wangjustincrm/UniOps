import { useRef, useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Plus, X, GripVertical, Search, Package } from 'lucide-react'
import { cn, formatCAD } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { PrLineItem, ProcurementType } from '@/types'
import { useUomCodes } from '@/hooks/useUoms'
import { useParts } from '@/hooks/useParts'
import type { ApiPart } from '@/services/parts'
import { PartThumbnail } from '@/pages/parts/PartsListPage'
import { useQuery } from '@tanstack/react-query'
import { mdmApi } from '@/lib/api'

function newLine(): PrLineItem {
  return {
    id: crypto.randomUUID(),
    description: '',
    materialId: '',
    supplierItemId: '',
    qty: 1,
    unit: 'pcs',
    unitPrice: 0,
    lineTotal: 0,
    notes: '',
  }
}

function recompute(item: PrLineItem): PrLineItem {
  return { ...item, lineTotal: Math.round(item.qty * item.unitPrice * 100) / 100 }
}

interface LineErrors {
  description?: string
  qty?: string
  unitPrice?: string
}

interface PrLineItemsProps {
  procurementType: ProcurementType | null
  items: PrLineItem[]
  onChange: (items: PrLineItem[]) => void
  errors?: Record<string, LineErrors>   // keyed by index string e.g. "0", "1"
}

const showMaterialId = (t: ProcurementType | null) => t === 1 || t === 3
const isSparePartsType = (t: ProcurementType | null) => t === 3

// ─── Parts Picker (Type 3 only) ──────────────────────────────────────────────

interface PartsPickerProps {
  value: string
  onSelect: (part: ApiPart) => void
  onClear: () => void
  hasError?: boolean
}

function PartsPicker({ value, onSelect, onClear, hasError }: PartsPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')

  const { data: partsData } = useParts({ active_only: true, search: query || undefined, page_size: 50 })
  const activeParts = partsData?.items ?? []
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0 })
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const filtered = activeParts

  const openDropdown = useCallback(() => {
    if (!inputRef.current) return
    const rect = inputRef.current.getBoundingClientRect()
    setPos({
      top: rect.bottom + window.scrollY + 4,
      left: rect.left + window.scrollX,
      width: Math.max(rect.width, 360),
    })
    setOpen(true)
  }, [])

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (
        !inputRef.current?.contains(e.target as Node) &&
        !dropdownRef.current?.contains(e.target as Node)
      ) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handleSelect = (part: ApiPart) => {
    onSelect(part)
    setOpen(false)
    setQuery('')
  }

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-1">
        <div
          className={cn(
            'flex h-8 flex-1 cursor-pointer items-center gap-2 rounded border bg-white px-2 text-sm transition-colors hover:border-primary-400',
            hasError ? 'border-danger-600' : 'border-neutral-300',
            open && 'border-primary-600 ring-1 ring-primary-600'
          )}
          onClick={openDropdown}
          ref={inputRef as unknown as React.RefObject<HTMLDivElement>}
        >
          <Search className="h-3 w-3 shrink-0 text-neutral-400" />
          <span className={cn('flex-1 truncate', value ? 'text-neutral-900' : 'text-neutral-400')}>
            {value || 'Search parts catalog…'}
          </span>
          {value && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onClear() }}
              className="text-neutral-300 hover:text-danger-500"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {open && createPortal(
        <div
          ref={dropdownRef}
          style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
          className="flex flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-[0_8px_24px_rgba(10,124,124,0.12)]"
        >
          {/* Search box inside dropdown */}
          <div className="border-b border-neutral-100 p-2">
            <div className="flex items-center gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-2">
              <Search className="h-3.5 w-3.5 text-neutral-400" />
              <input
                autoFocus
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name, code, category, supplier…"
                className="h-7 flex-1 bg-transparent text-sm focus:outline-none"
              />
              {query && (
                <button type="button" onClick={() => setQuery('')}>
                  <X className="h-3 w-3 text-neutral-400 hover:text-neutral-600" />
                </button>
              )}
            </div>
          </div>

          {/* Results */}
          <div className="max-h-60 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center gap-1 py-6 text-neutral-400">
                <Package className="h-5 w-5" />
                <span className="text-xs">No matching parts found</span>
              </div>
            ) : (
              filtered.map((part) => (
                <button
                  key={part.id}
                  type="button"
                  onClick={() => handleSelect(part)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-primary-50 transition-colors"
                >
                  <PartThumbnail src={part.image_data_url} size={32} />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-primary-600 shrink-0">{part.code}</span>
                      <span className="truncate text-xs font-medium text-neutral-900">{part.name}</span>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[10px] text-neutral-400">{part.category}</span>
                      <span className="text-[10px] text-neutral-300">·</span>
                      <span className="truncate text-[10px] text-neutral-500">{part.supplier}</span>
                      {part.supplier_part_no && (
                        <>
                          <span className="text-[10px] text-neutral-300">·</span>
                          <span className="font-mono text-[10px] text-neutral-400">{part.supplier_part_no}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <span className="amount shrink-0 text-xs font-medium text-neutral-700">
                    {formatCAD(part.unit_price)}/{part.unit}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}

// ─── ERP Materials Picker (Type 1 only) ──────────────────────────────────────

interface ErpMaterialOption {
  erp_part_no: string
  description: string | null
  unit_meas: string | null
  dim_quality: string | null
}

interface MaterialsPickerProps {
  value: string
  onSelect: (m: ErpMaterialOption) => void
  onClear: () => void
  hasError?: boolean
}

function MaterialsPicker({ value, onSelect, onClear, hasError }: MaterialsPickerProps) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const { data, isLoading } = useQuery<{ items: ErpMaterialOption[]; total: number }>({
    queryKey: ['erp-materials-picker', q],
    queryFn: () => mdmApi.get('/erp/materials', { part_status: 'A', search: q, page: 1, page_size: 30 }),
    enabled: open,
  })

  return (
    <div className="relative w-full" ref={ref}>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={cn(
            'h-8 flex-1 text-left rounded border bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600',
            hasError ? 'border-danger-600' : 'border-neutral-300'
          )}
        >
          {value || <span className="text-neutral-400">Pick ERP material…</span>}
        </button>
        {value && (
          <button type="button" onClick={onClear} className="text-neutral-300 hover:text-danger-500">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute top-full left-0 z-30 mt-1 w-[28rem] rounded-lg border border-neutral-200 bg-white shadow-lg">
          <div className="border-b border-neutral-100 p-2">
            <div className="flex items-center gap-2 rounded border border-neutral-200 px-2 py-1">
              <Search className="h-3.5 w-3.5 text-neutral-400" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search part no or description…"
                className="flex-1 text-xs focus:outline-none"
              />
            </div>
          </div>
          <div className="max-h-80 overflow-auto">
            {isLoading ? (
              <div className="px-2 py-3 text-xs text-neutral-400">Loading…</div>
            ) : !data?.items.length ? (
              <div className="px-2 py-3 text-xs text-neutral-400">No materials. Sync in AdminPanel → ERP MDM first.</div>
            ) : (
              data.items.map((m) => (
                <button
                  key={m.erp_part_no}
                  type="button"
                  onClick={() => { onSelect(m); setOpen(false); setQ('') }}
                  className="block w-full text-left px-3 py-2 hover:bg-neutral-50 border-b border-neutral-50"
                >
                  <div className="font-mono text-xs">{m.erp_part_no}</div>
                  <div className="text-xs text-neutral-700">{m.description}</div>
                  <div className="text-[10px] text-neutral-400">{m.unit_meas} · {m.dim_quality}</div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function PrLineItems({ procurementType, items, onChange, errors = {} }: PrLineItemsProps) {
  const hasMaterial = showMaterialId(procurementType)
  const isType3 = isSparePartsType(procurementType)
  const isType1 = procurementType === 1
  const lastRowRef = useRef<HTMLTableRowElement>(null)
  const uomCodes = useUomCodes()
  // Ensure a line's currently-saved unit is always selectable, even if it was
  // removed from the master list after the line was created.
  const unitOptions = (current: string): string[] =>
    current && !uomCodes.includes(current) ? [current, ...uomCodes] : uomCodes

  const update = (index: number, patch: Partial<PrLineItem>) => {
    const next = items.map((item, i) =>
      i === index ? recompute({ ...item, ...patch }) : item
    )
    onChange(next)
  }

  const selectPart = (index: number, part: ApiPart) => {
    update(index, {
      description: part.name,
      materialId: part.code,
      unitPrice: part.unit_price,
      unit: part.unit,
      supplierItemId: part.supplier_part_no ?? '',
    })
  }

  const clearPart = (index: number) => {
    update(index, {
      description: '',
      materialId: '',
      supplierItemId: '',
    })
  }

  const selectMaterial = (index: number, m: ErpMaterialOption) => {
    update(index, {
      description: m.description || items[index].description,
      materialId: m.erp_part_no,
      unit: m.unit_meas || items[index].unit,
      notes: m.dim_quality || items[index].notes,
    })
  }

  const clearMaterial = (index: number) => {
    update(index, { description: '', materialId: '', unit: 'pcs', notes: '' })
  }

  const addLine = () => {
    onChange([...items, newLine()])
    setTimeout(() => {
      lastRowRef.current
        ?.querySelector<HTMLInputElement>('input[data-field="description"]')
        ?.focus()
    }, 50)
  }

  const removeLine = (index: number) => {
    if (items.length <= 1) return
    onChange(items.filter((_, i) => i !== index))
  }

  const total = items.reduce((sum, item) => sum + item.lineTotal, 0)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-neutral-900">
            Line Items <span className="text-danger-600">*</span>
          </h3>
          {isType3 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-medium text-primary-700">
              <Package className="h-3 w-3" />
              Spare Parts — select from catalog
            </span>
          )}
        </div>
        <span className="text-xs text-neutral-400">{items.length} line{items.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Desktop table */}
      <div className="hidden md:block overflow-x-auto rounded-lg border border-neutral-200">
        <table className={cn('w-full text-sm', isType3 ? 'min-w-[740px]' : 'min-w-[860px]')}>
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="w-8 px-2 py-2.5 text-left text-xs font-semibold text-neutral-400">#</th>
              <th className={cn('px-3 py-2.5 text-left text-xs font-semibold text-neutral-600', isType3 ? 'min-w-[220px]' : 'min-w-[180px]')}>
                Description <span className="text-danger-600">*</span>
              </th>
              {hasMaterial && (
                <th className="w-28 px-3 py-2.5 text-left text-xs font-semibold text-neutral-600">
                  Material ID
                </th>
              )}
              <th className={cn('px-3 py-2.5 text-left text-xs font-semibold text-neutral-600', isType3 ? 'w-36' : 'w-44')}>
                Supplier Item ID
              </th>
              <th className="w-16 px-3 py-2.5 text-left text-xs font-semibold text-neutral-600">
                Qty <span className="text-danger-600">*</span>
              </th>
              <th className="w-16 px-3 py-2.5 text-left text-xs font-semibold text-neutral-600">Unit</th>
              <th className="w-28 px-3 py-2.5 text-right text-xs font-semibold text-neutral-600">
                Unit Price <span className="text-danger-600">*</span>
              </th>
              <th className="w-24 px-3 py-2.5 text-right text-xs font-semibold text-neutral-600">
                Line Total
              </th>
              {!isType3 && (
                <th className="w-28 px-3 py-2.5 text-left text-xs font-semibold text-neutral-400">Notes</th>
              )}
              <th className="w-8 px-2 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const isLast = i === items.length - 1
              const rowErrors = errors[String(i)] ?? {}
              return (
                <tr
                  key={item.id}
                  ref={isLast ? lastRowRef : undefined}
                  className={cn(
                    'border-b border-neutral-100 last:border-0',
                    i % 2 === 1 ? 'bg-neutral-50/50' : 'bg-white'
                  )}
                >
                  {/* Line number */}
                  <td className="px-2 py-2 text-xs text-neutral-400 select-none">
                    <div className="flex items-center gap-1">
                      <GripVertical className="h-3 w-3 text-neutral-300" />
                      {i + 1}
                    </div>
                  </td>

                  {/* Description — Type 3: parts picker; Type 1: ERP materials picker; others: free text */}
                  <td className="px-3 py-2">
                    {isType3 ? (
                      <>
                        <PartsPicker
                          value={item.description}
                          onSelect={(part) => selectPart(i, part)}
                          onClear={() => clearPart(i)}
                          hasError={!!rowErrors.description}
                        />
                        {rowErrors.description && (
                          <p className="mt-0.5 text-[10px] text-danger-600">{rowErrors.description}</p>
                        )}
                      </>
                    ) : isType1 ? (
                      <>
                        <MaterialsPicker
                          value={item.description}
                          onSelect={(m) => selectMaterial(i, m)}
                          onClear={() => clearMaterial(i)}
                          hasError={!!rowErrors.description}
                        />
                        {rowErrors.description && (
                          <p className="mt-0.5 text-[10px] text-danger-600">{rowErrors.description}</p>
                        )}
                      </>
                    ) : (
                      <>
                        <input
                          type="text"
                          data-field="description"
                          value={item.description}
                          onChange={(e) => update(i, { description: e.target.value })}
                          placeholder="Item description"
                          className={cn(
                            'h-8 w-full rounded border bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600',
                            rowErrors.description ? 'border-danger-600' : 'border-neutral-300'
                          )}
                          aria-invalid={!!rowErrors.description}
                        />
                        {rowErrors.description && (
                          <p className="mt-0.5 text-[10px] text-danger-600">{rowErrors.description}</p>
                        )}
                      </>
                    )}
                  </td>

                  {/* Material ID */}
                  {hasMaterial && (
                    <td className="px-3 py-2">
                      <input
                        type="text"
                        value={item.materialId ?? ''}
                        onChange={(e) => update(i, { materialId: e.target.value })}
                        readOnly={isType3 || isType1}
                        placeholder="MAT-XXXXXX"
                        className={cn(
                          'h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary-600',
                          (isType3 || isType1) && 'bg-neutral-50 text-neutral-500 cursor-default'
                        )}
                      />
                    </td>
                  )}

                  {/* Supplier Item ID */}
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={item.supplierItemId ?? ''}
                      onChange={(e) => update(i, { supplierItemId: e.target.value })}
                      placeholder="ASIN / SKU…"
                      className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary-600"
                    />
                  </td>

                  {/* Qty */}
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={item.qty || ''}
                      onChange={(e) => update(i, { qty: parseFloat(e.target.value) || 0 })}
                      className={cn(
                        'h-8 w-full rounded border bg-white px-2 text-sm text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-600',
                        rowErrors.qty ? 'border-danger-600' : 'border-neutral-300'
                      )}
                      aria-invalid={!!rowErrors.qty}
                    />
                  </td>

                  {/* Unit */}
                  <td className="px-3 py-2">
                    <select
                      value={item.unit}
                      onChange={(e) => update(i, { unit: e.target.value })}
                      className="h-8 w-full rounded border border-neutral-300 bg-white px-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                    >
                      {unitOptions(item.unit).map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
                    </select>
                  </td>

                  {/* Unit Price */}
                  <td className="px-3 py-2">
                    <div className="relative">
                      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-neutral-400">$</span>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={item.unitPrice || ''}
                        onChange={(e) => update(i, { unitPrice: parseFloat(e.target.value) || 0 })}
                        className={cn(
                          'h-8 w-full rounded border bg-white pl-5 pr-2 text-sm text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-600',
                          rowErrors.unitPrice ? 'border-danger-600' : 'border-neutral-300'
                        )}
                        aria-invalid={!!rowErrors.unitPrice}
                      />
                    </div>
                  </td>

                  {/* Line Total */}
                  <td className="px-3 py-2 text-right">
                    <span className={cn('amount text-sm', item.lineTotal > 0 ? 'text-neutral-900' : 'text-neutral-300')}>
                      {formatCAD(item.lineTotal)}
                    </span>
                  </td>

                  {/* Notes — hidden for Spare Parts; field still stored in state */}
                  {!isType3 && (
                    <td className="px-3 py-2">
                      <input
                        type="text"
                        value={item.notes ?? ''}
                        onChange={(e) => update(i, { notes: e.target.value })}
                        placeholder="Optional"
                        className="h-8 w-full rounded border border-neutral-300 bg-white px-2 text-sm text-neutral-500 focus:outline-none focus:ring-1 focus:ring-primary-600"
                      />
                    </td>
                  )}

                  {/* Remove */}
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => removeLine(i)}
                      disabled={items.length === 1}
                      className="flex h-7 w-7 items-center justify-center rounded text-neutral-300 hover:bg-danger-50 hover:text-danger-500 disabled:cursor-not-allowed disabled:opacity-30"
                      aria-label={`Remove line ${i + 1}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* Footer: add line + total */}
        <div className="flex items-center justify-between border-t border-neutral-200 bg-neutral-50 px-3 py-2.5">
          <Button type="button" variant="ghost" size="sm" onClick={addLine}>
            <Plus className="h-3.5 w-3.5" />
            Add Line
          </Button>
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-neutral-500">Total (CAD)</span>
            <span className={cn('amount text-base font-bold', total > 0 ? 'text-neutral-900' : 'text-neutral-300')}>
              {formatCAD(total)}
            </span>
          </div>
        </div>
      </div>

      {/* Mobile cards */}
      <div className="flex flex-col gap-3 md:hidden">
        {items.map((item, i) => {
          const rowErrors = errors[String(i)] ?? {}
          return (
            <div
              key={item.id}
              className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-3 flex flex-col gap-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-neutral-500">Line {i + 1}</span>
                <button
                  type="button"
                  onClick={() => removeLine(i)}
                  disabled={items.length === 1}
                  className="text-neutral-300 hover:text-danger-500 disabled:opacity-30"
                  aria-label={`Remove line ${i + 1}`}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-xs text-neutral-500">Description *</label>
                {isType3 ? (
                  <PartsPicker
                    value={item.description}
                    onSelect={(part) => selectPart(i, part)}
                    onClear={() => clearPart(i)}
                    hasError={!!rowErrors.description}
                  />
                ) : isType1 ? (
                  <MaterialsPicker
                    value={item.description}
                    onSelect={(m) => selectMaterial(i, m)}
                    onClear={() => clearMaterial(i)}
                    hasError={!!rowErrors.description}
                  />
                ) : (
                  <input
                    type="text"
                    data-field="description"
                    value={item.description}
                    onChange={(e) => update(i, { description: e.target.value })}
                    placeholder="Item description"
                    className={cn(
                      'h-9 w-full rounded border px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600',
                      rowErrors.description ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                )}
              </div>

              {hasMaterial && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-neutral-500">Material ID</label>
                  <input
                    type="text"
                    value={item.materialId ?? ''}
                    onChange={(e) => update(i, { materialId: e.target.value })}
                    readOnly={isType3 || isType1}
                    placeholder="MAT-XXXXXX"
                    className={cn(
                      'h-9 w-full rounded border border-neutral-300 px-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-primary-600',
                      (isType3 || isType1) && 'bg-neutral-50 text-neutral-500'
                    )}
                  />
                </div>
              )}

              <div className="flex flex-col gap-1">
                <label className="text-xs text-neutral-500">Supplier Item ID</label>
                <input
                  type="text"
                  value={item.supplierItemId ?? ''}
                  onChange={(e) => update(i, { supplierItemId: e.target.value })}
                  placeholder="ASIN / SKU…"
                  className="h-9 w-full rounded border border-neutral-300 px-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-neutral-500">Qty *</label>
                  <input
                    type="number"
                    min="0"
                    value={item.qty || ''}
                    onChange={(e) => update(i, { qty: parseFloat(e.target.value) || 0 })}
                    className="h-9 w-full rounded border border-neutral-300 px-2 text-right font-mono text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-neutral-500">Unit</label>
                  <select
                    value={item.unit}
                    onChange={(e) => update(i, { unit: e.target.value })}
                    className="h-9 w-full rounded border border-neutral-300 px-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                  >
                    {unitOptions(item.unit).map((u) => (
                      <option key={u} value={u}>{u}</option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-neutral-500">Unit Price *</label>
                  <div className="relative">
                    <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-neutral-400">$</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={item.unitPrice || ''}
                      onChange={(e) => update(i, { unitPrice: parseFloat(e.target.value) || 0 })}
                      className="h-9 w-full rounded border border-neutral-300 pl-5 pr-2 text-right font-mono text-sm focus:outline-none focus:ring-1 focus:ring-primary-600"
                    />
                  </div>
                </div>
              </div>

              {!isType3 && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-neutral-500">Notes</label>
                  <input
                    type="text"
                    value={item.notes ?? ''}
                    onChange={(e) => update(i, { notes: e.target.value })}
                    placeholder="Optional"
                    className="h-9 w-full rounded border border-neutral-300 px-2 text-sm text-neutral-500 focus:outline-none focus:ring-1 focus:ring-primary-600"
                  />
                </div>
              )}

              <div className="flex items-center justify-between rounded bg-neutral-50 px-2 py-1.5">
                <span className="text-xs text-neutral-500">Line Total</span>
                <span className="amount text-sm font-semibold text-neutral-900">{formatCAD(item.lineTotal)}</span>
              </div>
            </div>
          )
        })}

        <Button type="button" variant="secondary" size="sm" onClick={addLine} className="w-full">
          <Plus className="h-4 w-4" />
          Add Line
        </Button>

        <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3">
          <span className="text-sm font-semibold text-neutral-700">Total (CAD)</span>
          <span className={cn('amount text-lg font-bold', total > 0 ? 'text-neutral-900' : 'text-neutral-300')}>
            {formatCAD(total)}
          </span>
        </div>
      </div>
    </div>
  )
}

export function lineItemsTotal(items: PrLineItem[]): number {
  return items.reduce((sum, item) => sum + item.lineTotal, 0)
}

export function validateLineItems(items: PrLineItem[]): Record<string, LineErrors> {
  const errs: Record<string, LineErrors> = {}
  items.forEach((item, i) => {
    const e: LineErrors = {}
    if (!item.description.trim()) e.description = 'Required'
    if (!item.qty || item.qty <= 0) e.qty = 'Must be > 0'
    // unit price may be 0 or negative (discount / rebate / credit line); only a
    // missing / non-numeric value is an error. Applies to both PR and PO forms.
    if (!Number.isFinite(item.unitPrice)) e.unitPrice = 'Required'
    if (Object.keys(e).length) errs[String(i)] = e
  })
  return errs
}
