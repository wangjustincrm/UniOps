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

import { Input, MenuItem, PickerTrigger, Select, TBody, THead, Table, Td, Th, Tr, Badge, Card } from '@uniops/shell'
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

// Unit price may be 0 or negative (PRD §3.2.1 / §3.12.1), so the box shows the
// text being typed rather than re-deriving it from the number: `value || ''`
// turned a typed 0 into an empty box, which then looked like a missing price
// (ISS-0083). No `min`: a credit line is negative. A price set from outside —
// a part picked from the catalog, a PR copied or pre-filled — still replaces
// the text, because it no longer matches what was typed.
function UnitPriceInput({ value, onChange, ...rest }: {
  value: number
  onChange: (value: number) => void
  className?: string
  'aria-invalid'?: boolean
  error?: boolean
  inputSize?: 'sm' | 'md'
}) {
  const [text, setText] = useState(() => String(value))
  useEffect(() => {
    setText((t) => ((parseFloat(t) || 0) === value ? t : String(value)))
  }, [value])
  return (
    <Input
      type="number"
      step="0.01"
      value={text}
      onChange={(e) => { setText(e.target.value); onChange(parseFloat(e.target.value) || 0) }}
      onFocus={(e) => e.target.select()}
      {...rest}
    />
  )
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

// Exported so procurementTypes.test.ts can pin them: these two rules are the
// line-item half of "what differs between the types", and unlike the submit
// gates they live only here, where the backend's guide cannot derive them.
export const showMaterialId = (t: ProcurementType | null) => t === 1 || t === 3
export const isSparePartsType = (t: ProcurementType | null) => t === 3

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
            'flex h-8 flex-1 cursor-pointer items-center gap-2 rounded border bg-surface px-2 text-sm transition-colors hover:border-primary-400',
            hasError ? 'border-danger-solid' : 'border-line-strong',
            open && 'border-primary-600 ring-1 ring-primary-600'
          )}
          onClick={openDropdown}
          ref={inputRef as unknown as React.RefObject<HTMLDivElement>}
        >
          <Search className="h-3 w-3 shrink-0 text-fg-muted" />
          <span className={cn('flex-1 truncate', value ? 'text-fg' : 'text-fg-muted')}>
            {value || 'Search parts catalog…'}
          </span>
          {value && (
            <Button variant="danger-ghost" size="icon-xs" aria-label="Remove"
              type="button"
              onClick={(e) => { e.stopPropagation(); onClear() }}
             
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>

      {open && createPortal(
        <div
          ref={dropdownRef}
          style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
          className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-lg"
        >
          {/* Search box inside dropdown */}
          <div className="border-b border-line-subtle p-2">
            <div className="flex items-center gap-2 rounded-md border border-line-strong bg-surface-subtle px-2">
              <Search className="h-3.5 w-3.5 text-fg-muted" />
              <Input inputSize="sm"
                autoFocus
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name, code, category, supplier…"
                bare className="flex-1"
              />
              {query && (
                <Button variant="subtle" size="icon-xs" aria-label="Clear" type="button" onClick={() => setQuery('')}>
                  <X className="h-3 w-3 text-fg-muted hover:text-fg-secondary" />
                </Button>
              )}
            </div>
          </div>

          {/* Results */}
          <div className="max-h-60 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center gap-1 py-6 text-fg-muted">
                <Package className="h-5 w-5" />
                <span className="text-xs">No matching parts found</span>
              </div>
            ) : (
              filtered.map((part) => (
                <MenuItem
                  key={part.id}
                  type="button"
                  onClick={() => handleSelect(part)}
                  className="gap-3"
                >
                  <PartThumbnail src={part.image_data_url} size={32} />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-2xs text-primary-600 shrink-0">{part.code}</span>
                      <span className="truncate text-xs font-medium text-fg">{part.name}</span>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-2xs text-fg-muted">{part.category}</span>
                      <span className="text-2xs text-fg-faint">·</span>
                      <span className="truncate text-2xs text-fg-muted">{part.supplier}</span>
                      {part.supplier_part_no && (
                        <>
                          <span className="text-2xs text-fg-faint">·</span>
                          <span className="font-mono text-2xs text-fg-muted">{part.supplier_part_no}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <span className="amount shrink-0 text-xs font-medium text-fg-secondary">
                    {formatCAD(part.unit_price)}/{part.unit}
                  </span>
                </MenuItem>
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
        <PickerTrigger pickerSize="sm" error={hasError} className="flex-1" onClick={() => setOpen(true)} placeholder="Pick ERP material…">
          {value}
        </PickerTrigger>
        {value && (
          <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" type="button" onClick={onClear}>
            <X className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
      {open && (
        <div className="absolute top-full left-0 z-30 mt-1 w-[28rem] rounded-lg border border-line bg-surface shadow-lg">
          <div className="border-b border-line-subtle p-2">
            <div className="flex items-center gap-2 rounded border border-line px-2 py-1">
              <Search className="h-3.5 w-3.5 text-fg-muted" />
              <Input inputSize="sm"
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search part no or description…"
                className="flex-1"
              />
            </div>
          </div>
          <div className="max-h-80 overflow-auto">
            {isLoading ? (
              <div className="px-2 py-3 text-xs text-fg-muted">Loading…</div>
            ) : !data?.items.length ? (
              <div className="px-2 py-3 text-xs text-fg-muted">No materials. Sync in AdminPanel → ERP MDM first.</div>
            ) : (
              data.items.map((m) => (
                <MenuItem
                  key={m.erp_part_no}
                  type="button"
                  onClick={() => { onSelect(m); setOpen(false); setQ('') }}
                  className="block border-b border-line-subtle"
                >
                  <div className="font-mono text-xs">{m.erp_part_no}</div>
                  <div className="text-xs text-fg-secondary">{m.description}</div>
                  <div className="text-2xs text-fg-muted">{m.unit_meas} · {m.dim_quality}</div>
                </MenuItem>
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
          <h3 className="text-sm font-semibold text-fg">
            Line Items <span className="text-danger-fg">*</span>
          </h3>
          {isType3 && (
            <Badge variant="accent" size="sm">
              <Package className="h-3 w-3" />
              Spare Parts — select from catalog
            </Badge>
          )}
        </div>
        <span className="text-xs text-fg-muted">{items.length} line{items.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Desktop table */}
      <div className="hidden md:block overflow-x-auto rounded-lg border border-line">
        <Table density="compact" className={cn('w-full text-sm', isType3 ? 'min-w-[740px]' : 'min-w-[860px]')}>
          <THead>
            <Tr className="bg-surface-subtle">
              <Th className="w-8">#</Th>
              <Th className={cn('px-3 py-2.5 text-left text-xs font-semibold text-fg-secondary', isType3 ? 'min-w-[220px]' : 'min-w-[180px]')}>
                Description <span className="text-danger-fg">*</span>
              </Th>
              {hasMaterial && (
                <Th className="w-28">
                  Material ID
                </Th>
              )}
              <Th className={cn('px-3 py-2.5 text-left text-xs font-semibold text-fg-secondary', isType3 ? 'w-36' : 'w-44')}>
                Supplier Item ID
              </Th>
              <Th className="w-16">
                Qty <span className="text-danger-fg">*</span>
              </Th>
              {/* Wide enough to read the chosen unit: at w-16 the select showed
                  only its first letter beside the arrow (ISS-0225). */}
              <Th className="w-28">Unit</Th>
              <Th align="right" className="w-28 ">
                Unit Price <span className="text-danger-fg">*</span>
              </Th>
              <Th align="right" className="w-24 ">
                Line Total
              </Th>
              {!isType3 && (
                <Th className="w-28">Notes</Th>
              )}
              <Th className="w-8" />
            </Tr>
          </THead>
          <TBody>
            {items.map((item, i) => {
              const isLast = i === items.length - 1
              const rowErrors = errors[String(i)] ?? {}
              return (
                <Tr
                  key={item.id}
                  ref={isLast ? lastRowRef : undefined}
                  className={cn(
                    'border-b border-line-subtle last:border-0',
                    i % 2 === 1 ? 'bg-surface-subtle/50' : 'bg-surface'
                  )}
                >
                  {/* Line number */}
                  <Td className="text-xs text-fg-muted select-none">
                    <div className="flex items-center gap-1">
                      <GripVertical className="h-3 w-3 text-fg-faint" />
                      {i + 1}
                    </div>
                  </Td>

                  {/* Description — Type 3: parts picker; Type 1: ERP materials picker; others: free text */}
                  <Td>
                    {isType3 ? (
                      <>
                        <PartsPicker
                          value={item.description}
                          onSelect={(part) => selectPart(i, part)}
                          onClear={() => clearPart(i)}
                          hasError={!!rowErrors.description}
                        />
                        {rowErrors.description && (
                          <p className="mt-0.5 text-2xs text-danger-fg">{rowErrors.description}</p>
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
                          <p className="mt-0.5 text-2xs text-danger-fg">{rowErrors.description}</p>
                        )}
                      </>
                    ) : (
                      <>
                        <Input
                          type="text"
                          data-field="description"
                          value={item.description}
                          onChange={(e) => update(i, { description: e.target.value })}
                          placeholder="Item description"
                          error={!!rowErrors.description} inputSize="sm"
                          aria-invalid={!!rowErrors.description}
                        />
                        {rowErrors.description && (
                          <p className="mt-0.5 text-2xs text-danger-fg">{rowErrors.description}</p>
                        )}
                      </>
                    )}
                  </Td>

                  {/* Material ID */}
                  {hasMaterial && (
                    <Td>
                      <Input
                        type="text"
                        value={item.materialId ?? ''}
                        onChange={(e) => update(i, { materialId: e.target.value })}
                        readOnly={isType3 || isType1}
                        placeholder="MAT-XXXXXX"
                        inputSize="sm" className={cn('font-mono', (isType3 || isType1) && 'cursor-default')}
                      />
                    </Td>
                  )}

                  {/* Supplier Item ID */}
                  <Td>
                    <Input inputSize="sm"
                      type="text"
                      value={item.supplierItemId ?? ''}
                      onChange={(e) => update(i, { supplierItemId: e.target.value })}
                      placeholder="ASIN / SKU…"
                      className="w-full font-mono"
                    />
                  </Td>

                  {/* Qty */}
                  <Td>
                    <Input
                      type="number"
                      min="0"
                      step="1"
                      value={item.qty || ''}
                      onChange={(e) => update(i, { qty: parseFloat(e.target.value) || 0 })}
                      error={!!rowErrors.qty} inputSize="sm" className="text-right font-mono"
                      aria-invalid={!!rowErrors.qty}
                    />
                  </Td>

                  {/* Unit */}
                  <Td>
                    <Select selectSize="sm"
                      value={item.unit}
                      onChange={(e) => update(i, { unit: e.target.value })}
                      className="w-full min-w-[5rem]"
                    >
                      {unitOptions(item.unit).map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
                    </Select>
                  </Td>

                  {/* Unit Price */}
                  <Td>
                    <div className="relative">
                      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-fg-muted">$</span>
                      <UnitPriceInput
                        value={item.unitPrice}
                        onChange={(unitPrice) => update(i, { unitPrice })}
                        inputSize="sm"
                        error={!!rowErrors.unitPrice}
                        className="pl-5 pr-2 text-right font-mono"
                        aria-invalid={!!rowErrors.unitPrice}
                      />
                    </div>
                  </Td>

                  {/* Line Total */}
                  <Td align="right">
                    <span className={cn('amount text-sm', item.lineTotal > 0 ? 'text-fg' : 'text-fg-faint')}>
                      {formatCAD(item.lineTotal)}
                    </span>
                  </Td>

                  {/* Notes — hidden for Spare Parts; field still stored in state */}
                  {!isType3 && (
                    <Td>
                      <Input inputSize="sm"
                        type="text"
                        value={item.notes ?? ''}
                        onChange={(e) => update(i, { notes: e.target.value })}
                        placeholder="Optional"
                        className="w-full"
                      />
                    </Td>
                  )}

                  {/* Remove */}
                  <Td>
                    <Button variant="danger-ghost" size="icon-xs"
                      type="button"
                      onClick={() => removeLine(i)}
                      disabled={items.length === 1}
                     
                      aria-label={`Remove line ${i + 1}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  </Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>

        {/* Footer: add line + total */}
        <div className="flex items-center justify-between border-t border-line bg-surface-subtle px-3 py-2.5">
          <Button type="button" variant="ghost" size="sm" onClick={addLine}>
            <Plus className="h-3.5 w-3.5" />
            Add Line
          </Button>
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-fg-muted">Total (CAD)</span>
            <span className={cn('amount text-base font-semibold', total > 0 ? 'text-fg' : 'text-fg-faint')}>
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
              className="rounded-xl bg-surface shadow-sm p-3 flex flex-col gap-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-fg-muted">Line {i + 1}</span>
                <Button variant="danger-ghost" size="icon-xs"
                  type="button"
                  onClick={() => removeLine(i)}
                  disabled={items.length === 1}
                 
                  aria-label={`Remove line ${i + 1}`}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="flex flex-col gap-1">
                <label className="text-xs text-fg-muted">Description *</label>
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
                  <Input
                    type="text"
                    data-field="description"
                    value={item.description}
                    onChange={(e) => update(i, { description: e.target.value })}
                    placeholder="Item description"
                    error={!!rowErrors.description}
                  />
                )}
              </div>

              {hasMaterial && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-fg-muted">Material ID</label>
                  <Input
                    type="text"
                    value={item.materialId ?? ''}
                    onChange={(e) => update(i, { materialId: e.target.value })}
                    readOnly={isType3 || isType1}
                    placeholder="MAT-XXXXXX"
                    className="font-mono"
                  />
                </div>
              )}

              <div className="flex flex-col gap-1">
                <label className="text-xs text-fg-muted">Supplier Item ID</label>
                <Input inputSize="sm"
                  type="text"
                  value={item.supplierItemId ?? ''}
                  onChange={(e) => update(i, { supplierItemId: e.target.value })}
                  placeholder="ASIN / SKU…"
                  className="w-full font-mono"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-fg-muted">Qty *</label>
                  <Input inputSize="sm"
                    type="number"
                    min="0"
                    value={item.qty || ''}
                    onChange={(e) => update(i, { qty: parseFloat(e.target.value) || 0 })}
                    className="w-full text-right font-mono"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-fg-muted">Unit</label>
                  <Select selectSize="sm"
                    value={item.unit}
                    onChange={(e) => update(i, { unit: e.target.value })}
                    className="w-full"
                  >
                    {unitOptions(item.unit).map((u) => (
                      <option key={u} value={u}>{u}</option>
                    ))}
                  </Select>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-fg-muted">Unit Price *</label>
                  <div className="relative">
                    <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-fg-muted">$</span>
                    <UnitPriceInput
                      value={item.unitPrice}
                      onChange={(unitPrice) => update(i, { unitPrice })}
                      className="pl-5 pr-2 text-right font-mono"
                    />
                  </div>
                </div>
              </div>

              {!isType3 && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-fg-muted">Notes</label>
                  <Input inputSize="sm"
                    type="text"
                    value={item.notes ?? ''}
                    onChange={(e) => update(i, { notes: e.target.value })}
                    placeholder="Optional"
                    className="w-full"
                  />
                </div>
              )}

              <div className="flex items-center justify-between rounded bg-surface-subtle px-2 py-1.5">
                <span className="text-xs text-fg-muted">Line Total</span>
                <span className="amount text-sm font-semibold text-fg">{formatCAD(item.lineTotal)}</span>
              </div>
            </div>
          )
        })}

        <Button type="button" variant="secondary" size="sm" onClick={addLine} className="w-full">
          <Plus className="h-4 w-4" />
          Add Line
        </Button>

        <Card inset className="flex items-center justify-between px-4 py-3">
          <span className="text-sm font-semibold text-fg-secondary">Total (CAD)</span>
          <span className={cn('amount text-lg font-semibold', total > 0 ? 'text-fg' : 'text-fg-faint')}>
            {formatCAD(total)}
          </span>
        </Card>
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
