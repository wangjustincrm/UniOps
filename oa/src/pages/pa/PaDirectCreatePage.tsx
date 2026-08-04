import React, { useState, useCallback, useRef, useEffect } from 'react'
import { useReplaceTab } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery } from '@tanstack/react-query'
import {
  Upload, FileText, CheckCircle2, AlertTriangle, ArrowRight,
  ArrowLeft, Loader2, X, RefreshCw, Pencil, ChevronLeft, ChevronRight, Paperclip,
} from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api, budgetApi, epmsApi } from '@/lib/api'
import { useOaAuth } from '@/store/auth'
import { parseInvoiceFile, type ParsedInvoiceFields, type ParsedLineItem } from '@/lib/invoice-parser'
import { useUomCodes } from '@/hooks/useUoms'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).href

// ── Image Preview ─────────────────────────────────────────────────────────────

function ImagePreview({ file }: { file: File }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    const url = URL.createObjectURL(file)
    setSrc(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  return src
    ? <img src={src} alt="Invoice" className="max-w-full max-h-[500px] object-contain p-4" />
    : <div className="flex items-center justify-center h-full min-h-[500px] text-sm text-neutral-400"><Loader2 className="h-4 w-4 animate-spin" /></div>
}

// ── PDF Canvas Renderer ───────────────────────────────────────────────────────

function PdfPreview({ file }: { file: File }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [pageNum, setPageNum] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const pdfRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null)
  const renderTaskRef = useRef<pdfjsLib.RenderTask | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    file.arrayBuffer().then(async (buf) => {
      if (cancelled) return
      const pdf = await pdfjsLib.getDocument({ data: buf }).promise
      if (cancelled) { pdf.destroy(); return }
      pdfRef.current = pdf
      setTotalPages(pdf.numPages)
      setPageNum(1)
      setLoading(false)
    }).catch((e) => {
      if (!cancelled) { setError(String(e)); setLoading(false) }
    })

    return () => {
      cancelled = true
      renderTaskRef.current?.cancel()
      pdfRef.current?.destroy()
      pdfRef.current = null
    }
  }, [file])

  useEffect(() => {
    if (!pdfRef.current || loading) return
    const canvas = canvasRef.current
    if (!canvas) return

    let cancelled = false
    renderTaskRef.current?.cancel()

    pdfRef.current.getPage(pageNum).then((page) => {
      if (cancelled) return
      const viewport = page.getViewport({ scale: 2.0 })
      canvas.width = viewport.width
      canvas.height = viewport.height
      const ctx = canvas.getContext('2d')!
      const task = page.render({ canvas, canvasContext: ctx, viewport })
      renderTaskRef.current = task
      return task.promise
    }).catch(() => {})

    return () => { cancelled = true; renderTaskRef.current?.cancel() }
  }, [pageNum, loading])

  if (loading) return (
    <div className="flex items-center justify-center h-full min-h-[800px] gap-2 text-sm text-neutral-400">
      <Loader2 className="h-4 w-4 animate-spin" /> Rendering PDF…
    </div>
  )
  if (error) return (
    <div className="flex items-center justify-center h-full min-h-[800px] text-sm text-danger-500">
      Failed to render PDF
    </div>
  )

  return (
    <div className="flex flex-col items-center gap-2 p-2 w-full">
      <div className="overflow-auto w-full">
        <canvas ref={canvasRef} className="mx-auto rounded shadow-sm max-w-full" />
      </div>
      {totalPages > 1 && (
        <div className="flex items-center gap-3 text-sm text-neutral-600">
          <button onClick={() => setPageNum(p => Math.max(1, p - 1))} disabled={pageNum === 1}
            className="rounded p-1 hover:bg-neutral-100 disabled:opacity-30">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span>{pageNum} / {totalPages}</span>
          <button onClick={() => setPageNum(p => Math.min(totalPages, p + 1))} disabled={pageNum === totalPages}
            className="rounded p-1 hover:bg-neutral-100 disabled:opacity-30">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  )
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface InvoiceRecord { id: string; status: string; pa_number?: string | null }
interface DupError {
  message: string
  duplicate: { source: string; pa_number?: string; document_ref: string }
}

// ── Step indicator ────────────────────────────────────────────────────────────

function StepBar({ step }: { step: 1 | 2 | 3 }) {
  const steps = ['Upload Invoice', 'Review Data', 'Payment Details']
  return (
    <div className="flex items-center gap-0 mb-8">
      {steps.map((label, i) => {
        const n = i + 1
        const done = step > n
        const active = step === n
        return (
          <div key={label} className="flex items-center flex-1 last:flex-none">
            <div className="flex items-center gap-2 shrink-0">
              <div className={cn(
                'flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold transition-colors',
                done ? 'bg-primary-600 text-white' : active ? 'bg-primary-700 text-white' : 'bg-neutral-100 text-neutral-400',
              )}>
                {done ? <CheckCircle2 className="h-4 w-4" /> : n}
              </div>
              <span className={cn('text-sm font-medium hidden sm:block', active ? 'text-neutral-900' : 'text-neutral-400')}>
                {label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className={cn('flex-1 mx-3 h-px', step > n + 1 ? 'bg-primary-400' : 'bg-neutral-200')} />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: upload + client-side OCR ─────────────────────────────────────────

function Step1Upload({
  onDone, dupError, setDupError,
}: {
  onDone: (file: File, fields: ParsedInvoiceFields) => void
  dupError: DupError | null
  setDupError: (e: DupError | null) => void
}) {
  const [dragging, setDragging] = useState(false)
  const [status, setStatus] = useState<'idle' | 'reading' | 'ocr'>('idle')
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const process = useCallback(async (file: File) => {
    setError(''); setDupError(null)
    setStatus('reading')
    await new Promise(r => setTimeout(r, 50)) // let UI update

    setStatus('ocr')
    const result = await parseInvoiceFile(file)
    setStatus('idle')

    if (!result.ok) {
      setError(result.error)
      return
    }
    onDone(file, result.fields)
  }, [onDone, setDupError])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) process(f)
  }, [process])

  const loading = status !== 'idle'

  return (
    <div className="flex flex-col gap-5 max-w-lg mx-auto">
      <div>
        <h2 className="text-lg font-semibold text-neutral-900">Upload Invoice</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Upload the vendor invoice. AI will extract the details automatically.
        </p>
      </div>

      {dupError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-danger-500 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-danger-800">{dupError.message}</p>
              <p className="mt-1 text-xs text-danger-600 font-mono">
                {dupError.duplicate.pa_number || dupError.duplicate.document_ref}
              </p>
            </div>
            <button onClick={() => setDupError(null)} className="text-danger-400 hover:text-danger-600">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !loading && inputRef.current?.click()}
        className={cn(
          'flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-14 transition-colors',
          dragging ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 hover:border-primary-300 hover:bg-neutral-50',
          loading ? 'cursor-wait opacity-70' : 'cursor-pointer',
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.jpg,.jpeg,.png,.webp"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) process(f) }}
          disabled={loading}
        />
        {loading ? (
          <>
            <Loader2 className="h-10 w-10 text-primary-500 animate-spin mb-3" />
            <p className="text-sm font-medium text-neutral-700">
              {status === 'reading' ? 'Reading file…' : 'Extracting invoice data with AI…'}
            </p>
            <p className="text-xs text-neutral-400 mt-1">This takes a few seconds</p>
          </>
        ) : (
          <>
            <Upload className="h-10 w-10 text-neutral-300 mb-3" />
            <p className="text-sm font-medium text-neutral-700">Drop invoice here, or click to browse</p>
            <p className="text-xs text-neutral-400 mt-1">PDF, JPG, PNG, WebP · Max 25 MB</p>
          </>
        )}
      </div>

      {error && <ErrorBanner message={error} />}
    </div>
  )
}

// ── Step 2: review extracted fields ──────────────────────────────────────────

const FIELD_META: { key: keyof ParsedInvoiceFields; label: string; type?: string }[] = [
  { key: 'vendorName',          label: 'Vendor Name' },
  { key: 'vendorInvoiceNumber', label: 'Invoice Number' },
  { key: 'invoiceDate',         label: 'Invoice Date',  type: 'date' },
  { key: 'dueDate',             label: 'Due Date',      type: 'date' },
  { key: 'currency',            label: 'Currency' },
  { key: 'amount',              label: 'Subtotal',      type: 'number' },
  { key: 'taxAmount',           label: 'Tax Amount',    type: 'number' },
]

interface DbVendor { id: string; name: string; code: string }

function Step2Review({
  file, fields: initial, onConfirmed,
}: {
  file: File
  fields: ParsedInvoiceFields
  onConfirmed: (fields: ParsedInvoiceFields, vendorId: string | null, vendorName: string | null) => void
}) {
  const [fields, setFields] = useState<Record<string, string>>(() => {
    const m: Record<string, string> = {}
    for (const { key } of FIELD_META) {
      const v = initial[key]
      m[key] = v == null ? '' : String(v)
    }
    return m
  })
  const [lineItems, setLineItems] = useState<ParsedLineItem[]>(initial.lineItems ?? [])
  const [activeTab, setActiveTab] = useState<'data' | 'preview'>('data')
  const [selectedVendorId, setSelectedVendorId] = useState<string | null>(null)
  const [vendorSearchOpen, setVendorSearchOpen] = useState(false)
  const [vendorQuery, setVendorQuery] = useState('')
  const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')

  // Vendor matching — server-side search (same pattern as EPMS PR Create)
  const { data: vendorsData } = useQuery<{ items: DbVendor[] }>({
    queryKey: ['vendors-search', vendorQuery],
    queryFn: () => {
      const params = new URLSearchParams({ active_only: 'true', page_size: '50' })
      if (vendorQuery) params.set('search', vendorQuery)
      return api.get<{ items: DbVendor[] }>(`/api/v1/vendors?${params}`)
    },
  })
  const vendors = vendorsData?.items ?? []

  // Auto-match: seed vendorQuery with extracted name so server returns candidates
  useEffect(() => {
    if (initial.vendorName && !vendorQuery && !selectedVendorId) {
      setVendorQuery(initial.vendorName)
    }
  }, [])

  // Auto-select when server returns an exact or close match
  useEffect(() => {
    if (selectedVendorId || vendors.length === 0) return
    const extractedName = (initial.vendorName ?? '').trim().toLowerCase()
    if (extractedName.length < 3) return   // OCR 无名/太短 → 不自动匹配（避免 includes('') 恒真误配 vendors[0]）
    const exact = vendors.find(v => v.name.toLowerCase() === extractedName)
    const partial = vendors.find(v => {
      const n = v.name.toLowerCase()
      return n.includes(extractedName) || extractedName.includes(n)
    })
    const match = exact ?? partial
    if (match) setSelectedVendorId(match.id)
  }, [vendors])

  const selectedVendor = vendors.find(v => v.id === selectedVendorId) ?? null
  const filteredVendors = vendors

  // Duplicate pre-check: as soon as a vendor is matched and an invoice number is
  // present, ask the server whether this (vendor, invoice number) was already
  // recorded — so the user is warned here on Review, not at final submit.
  const invoiceNumber = fields.vendorInvoiceNumber?.trim() ?? ''
  const { data: dupCheck } = useQuery<{ duplicate: DupError['duplicate'] | null }>({
    queryKey: ['invoice-dup-check', selectedVendorId, invoiceNumber],
    queryFn: () => {
      const params = new URLSearchParams({ vendor_id: selectedVendorId!, invoice_number: invoiceNumber })
      return api.get<{ duplicate: DupError['duplicate'] | null }>(`/api/v1/invoices/check-duplicate?${params}`)
    },
    enabled: !!selectedVendorId && !!invoiceNumber,
  })
  const duplicate = dupCheck?.duplicate ?? null

  const uomCodes = useUomCodes()
  const unitOptions = (current: string | null): string[] =>
    current && !uomCodes.includes(current) ? [current, ...uomCodes] : uomCodes

  const updateLineItem = (i: number, patch: Partial<ParsedLineItem>) =>
    setLineItems(prev => prev.map((item, idx) => idx === i ? { ...item, ...patch } : item))

  const addLineItem = () =>
    setLineItems(prev => [...prev, { description: '', quantity: 1, unit: uomCodes[0] ?? null, unit_price: 0, line_total: 0 }])

  const removeLineItem = (i: number) =>
    setLineItems(prev => prev.filter((_, idx) => idx !== i))

  const allFilled = FIELD_META
    .filter(f => f.key === 'vendorName' || f.key === 'vendorInvoiceNumber' || f.key === 'amount')
    .every(f => fields[f.key]?.trim()) && !!selectedVendorId && !duplicate

  const handleConfirm = () => {
    const out: ParsedInvoiceFields = {
      ...initial,
      vendorName:          fields.vendorName || null,
      vendorInvoiceNumber: fields.vendorInvoiceNumber || null,
      invoiceDate:         fields.invoiceDate || null,
      dueDate:             fields.dueDate || null,
      currency:            fields.currency || 'CAD',
      amount:              fields.amount ? parseFloat(fields.amount) : null,
      taxAmount:           fields.taxAmount ? parseFloat(fields.taxAmount) : null,
      lineItems:           lineItems.length > 0 ? lineItems : null,
    }
    onConfirmed(out, selectedVendorId, selectedVendor?.name ?? null)
  }

  const extractedFields = FIELD_META.filter(f => initial[f.key] != null).length
  const extractedLineItems = initial.lineItems != null ? 1 : 0
  const extracted = extractedFields + extractedLineItems
  const total = FIELD_META.length + 1

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-semibold text-neutral-900">Review Extracted Data</h2>
        <div className="mt-1 flex items-center gap-3">
          <p className="text-sm text-neutral-500">
            AI extracted <span className="font-medium text-primary-700">{extracted}/{total}</span> fields from <span className="font-medium">{file.name}</span>
          </p>
          {extracted === total && (
            <span className="inline-flex items-center gap-1 rounded-full bg-success-100 px-2 py-0.5 text-[11px] font-semibold text-success-700">
              <CheckCircle2 className="h-3 w-3" /> High confidence
            </span>
          )}
        </div>
      </div>

      {duplicate && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-danger-500 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-danger-800">
                This invoice has already been uploaded.
              </p>
              <p className="mt-1 text-xs text-danger-600">
                Invoice <span className="font-mono">{invoiceNumber}</span> from this vendor is already
                recorded{duplicate.source === 'epms' ? ' in EPMS' : ''}
                {(duplicate.pa_number || duplicate.document_ref)
                  ? <> (<span className="font-mono">{duplicate.pa_number || duplicate.document_ref}</span>)</>
                  : null}.
                Change the invoice number or upload a different invoice.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Mobile tabs */}
      <div className="flex gap-1 border-b border-neutral-200 md:hidden">
        {(['data', 'preview'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={cn('px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab ? 'border-primary-700 text-primary-700' : 'border-transparent text-neutral-500')}>
            {tab === 'data' ? 'Extracted Data' : 'Preview'}
          </button>
        ))}
      </div>

      <div className="flex flex-col md:flex-row gap-6 items-start">
        {/* Left: file preview — fixed 800px */}
        <div className={cn(
          'w-full md:w-[720px] shrink-0 rounded-xl border border-neutral-200 overflow-hidden bg-neutral-50 min-h-[800px]',
          activeTab === 'preview' ? 'block' : 'hidden md:block',
          !isPdf && 'flex items-center justify-center',
        )}>
          {isPdf ? (
            <PdfPreview file={file} />
          ) : (
            <ImagePreview file={file} />
          )}
        </div>

        {/* Right: extracted fields — fixed 400px */}
        <div className={cn('w-full md:w-[520px] shrink-0 flex flex-col gap-3', activeTab === 'data' ? 'block' : 'hidden md:block')}>
          {FIELD_META.map(({ key, label, type }) => {
            const wasExtracted = initial[key] != null
            return (
              <React.Fragment key={key}>
                <div className={cn(
                  'rounded-lg border p-3',
                  !wasExtracted ? 'border-warning-200 bg-warning-50/40' : 'border-neutral-200 bg-white',
                )}>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-medium text-neutral-600">{label}</label>
                    {wasExtracted
                      ? <span className="text-[10px] font-semibold text-success-700 bg-success-100 rounded px-1.5 py-0.5">AI extracted ✓</span>
                      : <span className="text-[10px] font-semibold text-warning-700 bg-warning-100 rounded px-1.5 py-0.5 flex items-center gap-1"><Pencil className="h-2.5 w-2.5" />Enter manually</span>
                    }
                  </div>
                  <input
                    type={type === 'number' ? 'number' : type === 'date' ? 'date' : 'text'}
                    step={type === 'number' ? '0.01' : undefined}
                    value={fields[key]}
                    onChange={e => setFields(p => ({ ...p, [key]: e.target.value }))}
                    className={cn(
                      'w-full rounded border px-2.5 py-1.5 text-sm focus:outline-none focus:border-primary-400',
                      !wasExtracted ? 'border-warning-300 bg-warning-50' : 'border-neutral-200',
                    )}
                    placeholder={`Enter ${label.toLowerCase()}`}
                  />
                </div>

                {/* Total — inserted after Tax Amount */}
                {key === 'taxAmount' && (
                  <div className="rounded-lg border border-primary-200 bg-primary-50/40 p-3">
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-medium text-neutral-600">Total (Subtotal + Tax)</label>
                      <span className="text-[10px] font-semibold text-primary-700 bg-primary-100 rounded px-1.5 py-0.5">Calculated</span>
                    </div>
                    <p className="text-sm font-mono font-semibold text-primary-700 px-2.5 py-1.5">
                      {((parseFloat(fields['amount'] || '0') || 0) + (parseFloat(fields['taxAmount'] || '0') || 0)).toFixed(2)}
                      {fields['currency'] ? ` ${fields['currency']}` : ''}
                    </p>
                  </div>
                )}

                {/* Vendor Match — inserted after Vendor Name */}
                {key === 'vendorName' && (
                  <div className={cn(
                    'rounded-lg border p-3',
                    selectedVendor ? 'border-success-200 bg-success-50/40' : 'border-danger-200 bg-danger-50/40',
                  )}>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-medium text-neutral-600">Matched Vendor (DB)</label>
                      {selectedVendor
                        ? <span className="text-[10px] font-semibold text-success-700 bg-success-100 rounded px-1.5 py-0.5">Matched ✓</span>
                        : <span className="text-[10px] font-semibold text-danger-700 bg-danger-100 rounded px-1.5 py-0.5">⚠ No match — select manually</span>
                      }
                    </div>
                    {selectedVendor ? (
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-medium text-neutral-800">{selectedVendor.name}</p>
                          <p className="text-xs text-neutral-400">{selectedVendor.code}</p>
                        </div>
                        <button onClick={() => { setSelectedVendorId(null); setVendorQuery(''); setVendorSearchOpen(true) }}
                          className="text-xs text-neutral-400 hover:text-neutral-600 underline shrink-0">
                          Change
                        </button>
                      </div>
                    ) : (
                      <div className="relative">
                        <input
                          type="text"
                          placeholder="Search vendor by name or code…"
                          value={vendorQuery}
                          onFocus={() => setVendorSearchOpen(true)}
                          onChange={e => { setVendorQuery(e.target.value); setVendorSearchOpen(true) }}
                          className="w-full rounded border border-danger-300 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                        />
                        {vendorSearchOpen && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => setVendorSearchOpen(false)} />
                            <div className="absolute z-20 top-full mt-1 left-0 right-0 rounded-lg border border-neutral-200 bg-white shadow-lg max-h-48 overflow-y-auto">
                              {filteredVendors.length > 0 ? filteredVendors.map(v => (
                                <button key={v.id} type="button"
                                  onClick={() => { setSelectedVendorId(v.id); setVendorQuery(''); setVendorSearchOpen(false) }}
                                  className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left">
                                  <span className="font-mono text-xs bg-neutral-100 rounded px-1.5 py-0.5 text-neutral-600 shrink-0">{v.code}</span>
                                  {v.name}
                                </button>
                              )) : (
                                <p className="px-3 py-2 text-xs text-neutral-400">
                                  {vendors.length === 0 ? 'Loading vendors…' : 'No vendors match your search'}
                                </p>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </React.Fragment>
            )
          })}

          {/* Line Items */}
          <div className={cn(
            'rounded-lg border p-3',
            initial.lineItems != null ? 'border-neutral-200 bg-white' : 'border-warning-200 bg-warning-50/40',
          )}>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-neutral-600">Line Items</label>
              {initial.lineItems != null
                ? <span className="text-[10px] font-semibold text-success-700 bg-success-100 rounded px-1.5 py-0.5">AI extracted ✓</span>
                : <span className="text-[10px] font-semibold text-warning-700 bg-warning-100 rounded px-1.5 py-0.5 flex items-center gap-1"><Pencil className="h-2.5 w-2.5" />Enter manually</span>
              }
            </div>

            {lineItems.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                {/* Header */}
                <div className="grid grid-cols-[minmax(0,1fr)_48px_56px_72px_72px_20px] gap-1 px-1">
                  {['Description', 'Qty', 'Unit', 'Unit Price', 'Total', ''].map(h => (
                    <span key={h} className="text-[10px] font-semibold uppercase tracking-wide text-neutral-400">{h}</span>
                  ))}
                </div>
                {lineItems.map((item, i) => (
                  <div key={i} className="grid grid-cols-[minmax(0,1fr)_48px_56px_72px_72px_20px] gap-1 items-center">
                    <input
                      value={item.description}
                      onChange={e => updateLineItem(i, { description: e.target.value })}
                      className="rounded border border-neutral-200 px-1.5 py-1 text-xs focus:outline-none focus:border-primary-400"
                      placeholder="Description"
                    />
                    <input
                      type="number" min="0" step="1"
                      value={item.quantity}
                      onChange={e => {
                        const qty = parseFloat(e.target.value) || 0
                        updateLineItem(i, { quantity: qty, line_total: qty * item.unit_price })
                      }}
                      className="rounded border border-neutral-200 px-1.5 py-1 text-xs text-right focus:outline-none focus:border-primary-400"
                    />
                    <select
                      value={item.unit ?? ''}
                      onChange={e => updateLineItem(i, { unit: e.target.value || null })}
                      className="rounded border border-neutral-200 px-1 py-1 text-xs focus:outline-none focus:border-primary-400"
                    >
                      <option value="">—</option>
                      {unitOptions(item.unit).map(u => <option key={u} value={u}>{u}</option>)}
                    </select>
                    <input
                      type="number" min="0" step="0.01"
                      value={item.unit_price}
                      onChange={e => {
                        const up = parseFloat(e.target.value) || 0
                        updateLineItem(i, { unit_price: up, line_total: item.quantity * up })
                      }}
                      className="rounded border border-neutral-200 px-1.5 py-1 text-xs text-right focus:outline-none focus:border-primary-400"
                    />
                    <input
                      type="number" min="0" step="0.01"
                      value={item.line_total}
                      onChange={e => updateLineItem(i, { line_total: parseFloat(e.target.value) || 0 })}
                      className="rounded border border-neutral-200 px-1.5 py-1 text-xs text-right focus:outline-none focus:border-primary-400"
                    />
                    <button onClick={() => removeLineItem(i)} className="text-neutral-300 hover:text-danger-500 transition-colors">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
                {/* Subtotal row */}
                <div className="grid grid-cols-[minmax(0,1fr)_48px_56px_72px_72px_20px] gap-1 px-1 pt-1 border-t border-neutral-100">
                  <span className="col-span-4 text-[10px] text-neutral-400 text-right">Subtotal</span>
                  <span className="text-xs font-semibold text-neutral-700 text-right">
                    {lineItems.reduce((s, l) => s + l.line_total, 0).toFixed(2)}
                  </span>
                  <span />
                </div>
              </div>
            ) : (
              <p className="text-xs text-neutral-400 py-1">No line items extracted — add manually if needed.</p>
            )}

            <button
              onClick={addLineItem}
              className="mt-2 flex items-center gap-1 text-xs text-primary-700 hover:text-primary-900 font-medium"
            >
              <span className="text-base leading-none">+</span> Add line item
            </button>
          </div>

          {!selectedVendorId && (
            <p className="text-xs text-danger-700 bg-danger-50 rounded-lg px-3 py-2 border border-danger-200">
              ⚠ Please select a matched vendor from the database before continuing.
            </p>
          )}
          {selectedVendorId && !FIELD_META.filter(f => ['vendorName','vendorInvoiceNumber','amount'].includes(f.key)).every(f => fields[f.key]?.trim()) && (
            <p className="text-xs text-warning-700 bg-warning-50 rounded-lg px-3 py-2 border border-warning-200">
              Vendor Name, Invoice Number, and Subtotal are required.
            </p>
          )}

          <button
            onClick={handleConfirm}
            disabled={!allFilled}
            className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors mt-1"
          >
            <ArrowRight className="h-4 w-4" />
            Continue to Payment
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Step 3: PA form ───────────────────────────────────────────────────────────

function Step3PaForm({
  file, fields, vendorId, vendorName, onCreated,
}: {
  file: File
  fields: ParsedInvoiceFields
  vendorId: string | null
  vendorName: string | null
  onCreated: (paId: string) => void
}) {
  // Budget Account selection — two independent dropdowns:
  //  • Cost Center list ← epms-api  (Cost Centers are owned by EPMS)
  //  • L1 / L2 hierarchy ← budget-api  (Shared catalog, not CC-bound)
  interface BudgetL2 { id: string; code: string; name: string }
  interface BudgetL1 { id: string; code: string; name: string; accounts: BudgetL2[] }
  interface CostCenter { id: string; code: string; name: string; is_active: boolean }

  const { user } = useOaAuth()
  const deptId = user?.department_id ?? null
  const { data: costCenters = [] } = useQuery<CostCenter[]>({
    queryKey: ['epms-cost-centers', deptId],
    queryFn: () => {
      const qs = deptId
        ? `/api/v1/cost-centers?active_only=true&department_id=${deptId}`
        : '/api/v1/cost-centers?active_only=true'
      return epmsApi.get<CostCenter[]>(qs)
    },
  })

  const { data: budgetHierarchy } = useQuery<{ l1_groups: BudgetL1[] }>({
    queryKey: ['budget-hierarchy'],
    queryFn: () => budgetApi.get<{ l1_groups: BudgetL1[] }>('/hierarchy'),
  })
  const l1Groups: BudgetL1[] = budgetHierarchy?.l1_groups ?? []

  const [selectedCostCenterId, setSelectedCostCenterId] = useState('')
  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL2, setSelectedL2] = useState('')

  const selectedL1Obj = l1Groups.find(l1 => l1.code === selectedL1)
  const l2Accounts = selectedL1Obj?.accounts ?? []

  const [title, setTitle] = useState(
    `Direct Payment — ${vendorName ?? 'Vendor'} ${fields.vendorInvoiceNumber ?? ''}`.trim()
  )
  const [notes, setNotes] = useState('')
  const [extraFiles, setExtraFiles] = useState<File[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [createdInvoiceId, setCreatedInvoiceId] = useState<string | null>(null)
  const [attachmentsUploaded, setAttachmentsUploaded] = useState(false)
  const extraFileInputRef = useRef<HTMLInputElement>(null)

  const total = (fields.amount ?? 0) + (fields.taxAmount ?? 0)

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true); setError('')
    try {
      // 1. 建发票（重试时复用已建的，避免 dedup 409 锁死）
      let invoiceId = createdInvoiceId
      if (!invoiceId) {
        const inv = await api.post<InvoiceRecord>('/api/v1/invoices', {
          file_name: file.name,
          file_mime_type: file.type || 'application/octet-stream',
          file_size_bytes: file.size,
          invoice_number: fields.vendorInvoiceNumber || null,
          vendor_id: vendorId || null,
          vendor_name: vendorName || null,
          invoice_date: fields.invoiceDate || null,
          due_date: fields.dueDate || null,
          currency: fields.currency || 'CAD',
          subtotal: fields.amount ?? 0,
          tax_amount: fields.taxAmount ?? 0,
          total_amount: total,
          lines: (fields.lineItems ?? []).map((li, i) => ({
            line_number: i + 1,
            description: li.description,
            quantity: li.quantity,
            unit: li.unit,
            unit_price: li.unit_price,
            amount: li.line_total,
            tax_amount: 0,
          })),
        })
        invoiceId = inv.id
        setCreatedInvoiceId(invoiceId)
      }

      // 2. 上传发票文件 + 附加附件（non-fatal，只传一次）
      if (!attachmentsUploaded) {
        const uploadAttachment = async (f: File) => {
          const form = new FormData()
          form.append('file', f)
          await api.postForm(`/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=oa`, form)
        }
        await uploadAttachment(file).catch(() => {})
        for (const f of extraFiles) { await uploadAttachment(f).catch(() => {}) }
        setAttachmentsUploaded(true)
      }

      // 3. 建 PA-DIR
      const pa = await api.post<{ id: string; pa_number: string }>('/api/v1/pa/direct', {
        invoice_id: invoiceId,
        vendor_id: vendorId,
        vendor_name: vendorName,
        payment_amount: total,
        currency: fields.currency || 'CAD',
        notes: notes || null,
        title: title.trim() || null,
        budget_account_code: selectedL2 || null,
        cost_center_id: selectedCostCenterId || null,
      })

      onCreated(pa.id)
    } catch (err: any) {
      setError(err.message || 'Failed to create PA')
    } finally { setSaving(false) }
  }

  return (
    <form onSubmit={handleCreate} className="flex flex-col gap-5 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold text-neutral-900">Payment Details</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Review and confirm before creating the payment application.
          Finance Manager approval will be required.
        </p>
      </div>

      {/* Invoice summary */}
      <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4 flex items-start gap-3">
        <FileText className="h-5 w-5 text-primary-600 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-neutral-800">{file.name}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-0.5 text-xs text-neutral-500">
            {fields.vendorInvoiceNumber && <span>#{fields.vendorInvoiceNumber}</span>}
            {fields.invoiceDate && <span>{formatDate(fields.invoiceDate)}</span>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className="text-sm font-mono font-semibold">{formatAmount(total, fields.currency || 'CAD')}</p>
          <p className="text-[11px] text-neutral-400">Total</p>
        </div>
      </div>

      {/* Title */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Title *</label>
        <input value={title} onChange={e => setTitle(e.target.value)} required
          placeholder="e.g. Direct Payment — Vendor Name Invoice #..."
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
      </div>

      {/* Description — background / reason for this payment */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Description</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
          placeholder="Background and reason for this payment…"
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none" />
      </div>

      {/* Budget Account — CC from epms-api, L1/L2 from budget-api (shared catalog) */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-2">
        <label className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Cost Center & Budget Account</label>
        <select
          value={selectedCostCenterId}
          onChange={e => setSelectedCostCenterId(e.target.value)}
          className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          <option value="">Select Cost Center…</option>
          {costCenters.map(cc => (
            <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>
          ))}
        </select>
        <select
          value={selectedL1}
          onChange={e => { setSelectedL1(e.target.value); setSelectedL2('') }}
          className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          <option value="">Select L1 Category…</option>
          {l1Groups.map(l1 => <option key={l1.id} value={l1.code}>{l1.code} — {l1.name}</option>)}
        </select>
        <select
          value={selectedL2}
          onChange={e => setSelectedL2(e.target.value)}
          disabled={!selectedL1}
          className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:bg-neutral-50 disabled:text-neutral-400"
        >
          <option value="">Select L2 Account…</option>
          {l2Accounts.map(a => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
        </select>
        {selectedL2 && selectedCostCenterId && (
          <p className="text-xs text-neutral-500">
            Selected: <span className="font-mono font-medium">{selectedL2}</span>
            {' '}@ CC <span className="font-mono font-medium">
              {costCenters.find(cc => cc.id === selectedCostCenterId)?.code}
            </span>
          </p>
        )}
      </div>

      {/* Vendor Name — matched DB vendor (read-only) */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Vendor Name</label>
        <div className="w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-800">
          {vendorName ?? '—'}
        </div>
      </div>

      {/* Line Items */}
      {fields.lineItems && fields.lineItems.length > 0 && (
        <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
          <div className="px-4 py-2.5 border-b border-neutral-100 bg-neutral-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Line Items <span className="ml-1 font-normal normal-case text-neutral-400">({fields.lineItems.length})</span>
            </h3>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-100">
                <th className="px-4 py-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wide">Description</th>
                <th className="px-3 py-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-14">Qty</th>
                <th className="px-3 py-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-24">Unit Price</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-24">Total</th>
              </tr>
            </thead>
            <tbody>
              {fields.lineItems.map((li, i) => (
                <tr key={i} className="border-b border-neutral-50 last:border-0">
                  <td className="px-4 py-2.5 text-neutral-800">{li.description}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">
                    {li.quantity}{li.unit ? ` ${li.unit}` : ''}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">
                    {formatAmount(li.unit_price, fields.currency || 'CAD')}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(li.line_total, fields.currency || 'CAD')}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-neutral-200 bg-neutral-50">
                <td colSpan={3} className="px-4 py-2 text-xs text-neutral-400 text-right">Lines subtotal</td>
                <td className="px-4 py-2 text-right font-mono text-xs font-semibold text-neutral-700">
                  {formatAmount(fields.lineItems.reduce((s, l) => s + l.line_total, 0), fields.currency || 'CAD')}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* Amount breakdown */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 space-y-1.5">
        <div className="flex justify-between text-sm">
          <span className="text-neutral-500">Subtotal</span>
          <span className="font-mono">{formatAmount(fields.amount ?? 0, fields.currency || 'CAD')}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-neutral-500">Tax</span>
          <span className="font-mono">{formatAmount(fields.taxAmount ?? 0, fields.currency || 'CAD')}</span>
        </div>
        <div className="flex justify-between text-sm font-semibold border-t border-neutral-100 pt-2 mt-1">
          <span>Payment Amount</span>
          <span className="font-mono text-primary-700">{formatAmount(total, fields.currency || 'CAD')}</span>
        </div>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Additional Attachments</label>
          <button type="button" onClick={() => extraFileInputRef.current?.click()}
            className="text-xs font-medium text-primary-700 hover:text-primary-900 flex items-center gap-1">
            <span className="text-base leading-none">+</span> Add file
          </button>
          <input ref={extraFileInputRef} type="file" multiple className="hidden"
            onChange={e => { if (e.target.files) setExtraFiles(prev => [...prev, ...Array.from(e.target.files!)]) }} />
        </div>
        {extraFiles.length === 0 ? (
          <p className="text-xs text-neutral-400">No additional attachments. Click "Add file" to upload supporting documents.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {extraFiles.map((f, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm">
                <Paperclip className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
                <span className="flex-1 truncate text-neutral-700 text-xs">{f.name}</span>
                <span className="text-xs text-neutral-400">{(f.size / 1024).toFixed(0)} KB</span>
                <button type="button" onClick={() => setExtraFiles(prev => prev.filter((_, j) => j !== i))}
                  className="text-neutral-300 hover:text-danger-500 transition-colors">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-start gap-3 rounded-lg border border-info-200 bg-info-50 px-4 py-3 text-sm">
        <AlertTriangle className="h-4 w-4 text-info-500 shrink-0 mt-0.5" />
        <p className="text-info-700">Direct payments always require <strong>Finance Manager</strong> approval.</p>
      </div>

      {error && <ErrorBanner message={error} />}

      <button type="submit" disabled={saving || !vendorName}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors">
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
        {saving ? 'Creating…' : 'Create Payment Application'}
      </button>
    </form>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PaDirectCreatePage() {
  const replaceTab = useReplaceTab(oaRoutes)
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [file, setFile] = useState<File | null>(null)
  const [fields, setFields] = useState<ParsedInvoiceFields | null>(null)
  const [vendorId, setVendorId] = useState<string | null>(null)
  const [vendorName, setVendorName] = useState<string | null>(null)
  const [dupError, setDupError] = useState<DupError | null>(null)

  return (
    <div className="flex flex-col gap-6 max-w-[1320px]">
      <div>
        <a href="/pa" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to PA List
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Direct Payment Application</h1>
        <p className="mt-0.5 text-sm text-neutral-500">Vendor payment without a linked purchase order</p>
      </div>

      <StepBar step={step} />

      {step === 1 && (
        <Step1Upload
          dupError={dupError}
          setDupError={setDupError}
          onDone={(f, flds) => { setFile(f); setFields(flds); setStep(2) }}
        />
      )}

      {step === 2 && file && fields && (
        <>
          <Step2Review
            file={file}
            fields={fields}
            onConfirmed={(updated, vid, vname) => { setFields(updated); setVendorId(vid); setVendorName(vname); setStep(3) }}
          />
          <button onClick={() => { setFile(null); setFields(null); setStep(1) }}
            className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 self-start">
            <RefreshCw className="h-3.5 w-3.5" />Upload different file
          </button>
        </>
      )}

      {step === 3 && file && fields && (
        <>
          <Step3PaForm file={file} fields={fields} vendorId={vendorId} vendorName={vendorName} onCreated={id => replaceTab(`/pa/${id}`)} />
          <button onClick={() => setStep(2)}
            className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 self-start">
            <ArrowLeft className="h-3.5 w-3.5" />Back to review
          </button>
        </>
      )}
    </div>
  )
}
