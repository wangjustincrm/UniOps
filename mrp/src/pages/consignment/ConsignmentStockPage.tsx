// Consignment Stock — design spec §6.6 page 4. Lightweight weekly entry
// form (product / lot number / quantity / count date) + this period's
// entries list, for the single 'MAIN' consignment (代储仓) warehouse (task
// 11 brief; backend is mrp-api's /consignment/* — app/api/v1/consignment.py).
//
// Key interaction: lot number onBlur triggers a WMS lot lookup
// (GET .../lot-lookup). A hit auto-fills production/expiry date, shown
// read-only with a "from WMS" marker; a miss shows a non-blocking notice
// ("expiry will be left blank") — the backend's own create endpoint already
// treats a WMS miss as a normal outcome (POST .../stock never requires
// expiry_date), so the page must never gate Save on the lookup result.
//
// Decimal fields (qty) arrive from the API as JSON strings — see
// consignmentApi.ts header — converted with Number() at display/arithmetic
// sites, never trusted as numbers on the wire.
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Save, CheckCircle2, HelpCircle } from 'lucide-react'
import { Button, Input, FormField, Badge } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { MaterialPicker } from './MaterialPicker'
import { LotCombo } from './LotCombo'
import { consignmentApi, type ConsignmentStock, type LotHistoryItem } from './consignmentApi'
import type { MaterialOption } from '@/lib/materials'

const MAIN_WAREHOUSE = 'MAIN'
const STALE_AFTER_DAYS = 7

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Whole-day difference between an ISO 'YYYY-MM-DD' date and today, local time. */
function daysSince(isoDate: string): number {
  const [y, m, d] = isoDate.split('-').map(Number)
  const then = new Date(y, (m ?? 1) - 1, d ?? 1)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((startOfToday.getTime() - then.getTime()) / 86_400_000)
}

function daysAgoLabel(n: number): string {
  if (n <= 0) return 'today'
  if (n === 1) return '1 day ago'
  return `${n} days ago`
}

type LotLookupState = 'idle' | 'loading' | 'found' | 'not-found' | 'error'

interface FormErrors {
  material?: string
  lotNo?: string
  qty?: string
  countDate?: string
}

export default function ConsignmentStockPage() {
  const queryClient = useQueryClient()
  const toasts = useToasts()

  // ── Entry form state ────────────────────────────────────────────────────
  const [materialCode, setMaterialCode] = useState('')
  const [materialLabel, setMaterialLabel] = useState('')
  const [lotNo, setLotNo] = useState('')
  const [qty, setQty] = useState('')
  // Persisted across submissions (not reset with the rest of the form) —
  // a weekly count session is many lots entered against the *same* date, so
  // re-picking the date for every single lot would be pure friction.
  const [countDate, setCountDate] = useState(todayIso())
  const [fieldErrors, setFieldErrors] = useState<FormErrors>({})
  const [createError, setCreateError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // ── Lot lookup state (driven by the lot-number field's onBlur) ─────────
  const [lookupState, setLookupState] = useState<LotLookupState>('idle')
  const [lookupDates, setLookupDates] = useState<{ production_date: string | null; expiry_date: string | null } | null>(null)

  function resetLookup() {
    setLookupState('idle')
    setLookupDates(null)
  }

  function handleMaterialSelect(m: MaterialOption) {
    setMaterialCode(m.code)
    setMaterialLabel(m.name ? `${m.code} — ${m.name}` : m.code)
    setFieldErrors((e) => ({ ...e, material: undefined }))
    resetLookup() // a lookup already run was keyed to the previous product
  }

  function handleMaterialClear() {
    setMaterialCode('')
    setMaterialLabel('')
    resetLookup()
  }

  function handleLotChange(v: string) {
    setLotNo(v)
    setFieldErrors((e) => ({ ...e, lotNo: undefined }))
    if (lookupState !== 'idle') resetLookup() // stale result for a now-edited lot number
  }

  function handleLotPick(item: LotHistoryItem) {
    // Chosen from the WMS history combo — we already hold its dates, so show
    // the "found in WMS" state directly instead of firing another lookup.
    setLotNo(item.lot_no)
    setFieldErrors((e) => ({ ...e, lotNo: undefined }))
    setLookupDates({ production_date: item.production_date, expiry_date: item.expiry_date })
    setLookupState('found')
  }

  async function handleLotBlur() {
    const lot = lotNo.trim()
    // Nothing to look up without both halves of the (material, lot) key the
    // backend's lot-lookup endpoint requires — silently wait for the other
    // field rather than erroring on an incomplete form.
    if (!materialCode || !lot) return
    setLookupState('loading')
    try {
      const res = await consignmentApi.lotLookup(lot, materialCode)
      if (res.found) {
        setLookupDates({ production_date: res.production_date, expiry_date: res.expiry_date })
        setLookupState('found')
      } else {
        setLookupDates(null)
        setLookupState('not-found')
      }
    } catch {
      // A lookup failure (WMS/network hiccup) must never block data entry —
      // matches lookup_lot()'s own contract of never raising. Leave expiry
      // unresolved; POST .../stock will simply attempt its own lookup again.
      setLookupDates(null)
      setLookupState('error')
    }
  }

  function validate(): FormErrors {
    const errs: FormErrors = {}
    if (!materialCode) errs.material = 'Select a product.'
    if (!lotNo.trim()) errs.lotNo = 'Enter a lot number.'
    const qtyNum = Number(qty)
    if (qty.trim() === '' || !Number.isFinite(qtyNum) || qtyNum < 0) {
      errs.qty = 'Enter a quantity of 0 or more.'
    }
    if (!countDate) errs.countDate = 'Select a count date.'
    return errs
  }

  function resetEntryFields() {
    setMaterialCode('')
    setMaterialLabel('')
    setLotNo('')
    setQty('')
    resetLookup()
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const errs = validate()
    setFieldErrors(errs)
    if (Object.keys(errs).length > 0) return

    setSubmitting(true)
    setCreateError(null)
    try {
      const res = await consignmentApi.createStock({
        material_code: materialCode,
        lot_no: lotNo.trim(),
        qty: Number(qty),
        count_date: countDate,
      })
      toasts.success(`Saved ${res.material_code} · lot ${res.lot_no} · ${formatQty(Number(res.qty))}.`)
      resetEntryFields()
      await queryClient.invalidateQueries({ queryKey: ['consignment-stock'] })
    } catch (err) {
      // Duplicate (warehouse, material, lot, count_date) comes back as a 409
      // whose `detail` is already a readable sentence (see consignment.py) —
      // ApiError.message carries that through as-is, so this one path
      // covers both the 409 case the brief calls out and any other create
      // failure, without special-casing the status code.
      setCreateError(errMsg(err, 'Could not save this entry — please retry.'))
    } finally {
      setSubmitting(false)
    }
  }

  // ── This period's entries ───────────────────────────────────────────────
  const stockQuery = useQuery({
    queryKey: ['consignment-stock'],
    queryFn: () => consignmentApi.listStock(1, 100),
  })

  const latestCountDate = stockQuery.data?.latest_count_dates[MAIN_WAREHOUSE] ?? null
  const staleDays = latestCountDate ? daysSince(latestCountDate) : null
  const isStale = staleDays !== null && staleDays > STALE_AFTER_DAYS

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-neutral-900">Consignment Stock</h1>

      {/* ── Entry form ─────────────────────────────────────────────────── */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-neutral-800">New Count Entry</h2>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FormField label="Product" required error={fieldErrors.material}>
            <MaterialPicker
              value={materialLabel}
              onSelect={handleMaterialSelect}
              onClear={handleMaterialClear}
              hasError={!!fieldErrors.material}
              disabled={submitting}
            />
          </FormField>

          <FormField label="Lot Number" required error={fieldErrors.lotNo}>
            <LotCombo
              materialCode={materialCode}
              value={lotNo}
              onChange={handleLotChange}
              onPick={handleLotPick}
              onBlur={handleLotBlur}
              hasError={!!fieldErrors.lotNo}
              disabled={submitting}
            />
          </FormField>

          <FormField label="Quantity" required htmlFor="qty" error={fieldErrors.qty}>
            <Input
              id="qty"
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={qty}
              onChange={(e) => { setQty(e.target.value); setFieldErrors((er) => ({ ...er, qty: undefined })) }}
              placeholder="0"
              disabled={submitting}
              error={!!fieldErrors.qty}
            />
          </FormField>

          <FormField label="Count Date" required htmlFor="count-date" error={fieldErrors.countDate}>
            <Input
              id="count-date"
              type="date"
              value={countDate}
              onChange={(e) => { setCountDate(e.target.value); setFieldErrors((er) => ({ ...er, countDate: undefined })) }}
              disabled={submitting}
              error={!!fieldErrors.countDate}
            />
          </FormField>
        </div>

        {/* Lot lookup feedback — role="status" (polite, non-interrupting):
            these are informational outcomes of a lookup the user triggered
            by leaving the field, not form errors, and per the design spec
            a WMS miss must read as "here's what happens next", not a
            failure. `lookupState === 'error'` is the one case that *is*
            a genuine failure (network/WMS unreachable) and gets role="alert"
            instead, below. */}
        {lookupState === 'loading' && (
          <p role="status" className="flex items-center gap-1.5 text-xs text-neutral-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking WMS for this lot…
          </p>
        )}
        {lookupState === 'found' && lookupDates && (
          <div role="status" className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-success-200 bg-success-50 px-3 py-2 text-xs text-success-800">
            <span className="flex items-center gap-1.5 font-medium">
              <CheckCircle2 className="h-3.5 w-3.5" /> Lot found in WMS
              <Badge variant="success">from WMS</Badge>
            </span>
            <span>Production date: <strong>{lookupDates.production_date ? formatDate(lookupDates.production_date) : '—'}</strong></span>
            <span>Expiry date: <strong>{lookupDates.expiry_date ? formatDate(lookupDates.expiry_date) : '—'}</strong></span>
          </div>
        )}
        {lookupState === 'not-found' && (
          <p role="status" className="flex items-center gap-1.5 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
            <HelpCircle className="h-3.5 w-3.5 shrink-0" />
            Lot not found in WMS — expiry will be left blank. You can still save this entry.
          </p>
        )}
        {lookupState === 'error' && (
          <p role="alert" className="flex items-center gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            Could not reach the WMS lot lookup — expiry will be left blank. You can still save this entry.
          </p>
        )}

        {createError && (
          <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            <AlertTriangle className="h-4 w-4 shrink-0" /> {createError}
          </p>
        )}

        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={submitting}>
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Save Entry
          </Button>
        </div>
      </form>

      {/* ── This period's entries ─────────────────────────────────────── */}
      <div className="flex flex-col gap-2">
        {latestCountDate && (
          <div
            className={cn(
              'flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm',
              isStale ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-neutral-200 bg-neutral-50 text-neutral-600',
            )}
          >
            {isStale && <AlertTriangle className="h-4 w-4 shrink-0" />}
            <span>
              Last counted: <strong>{formatDate(latestCountDate)}</strong> ({daysAgoLabel(staleDays ?? 0)})
            </span>
          </div>
        )}

        {stockQuery.isError && (
          <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {errMsg(stockQuery.error, 'Could not load consignment stock entries.')}
          </p>
        )}

        {stockQuery.isLoading ? (
          <p role="status" className="py-10 text-center text-sm text-neutral-400">Loading entries…</p>
        ) : (
          <ConsignmentEntriesTable items={stockQuery.data?.items ?? []} />
        )}
      </div>

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}

function expirySourceBadge(source: ConsignmentStock['expiry_source']) {
  if (source === 'wms') return <Badge variant="success">from WMS</Badge>
  if (source === 'manual') return <Badge variant="neutral">manual</Badge>
  return <span className="text-neutral-300">—</span>
}

function ConsignmentEntriesTable({ items }: { items: ConsignmentStock[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-12 text-center">
        <p className="text-sm text-neutral-500">No consignment stock entries yet.</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200">
      <table className="min-w-full text-sm">
        <thead className="bg-neutral-50">
          <tr>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Product</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Lot No.</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Qty</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Count Date</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Expiry Date</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Source</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.id} className="border-b border-neutral-100 last:border-0 odd:bg-white even:bg-neutral-50/50">
              <td className="px-3 py-2 font-mono text-xs text-neutral-800">{row.material_code}</td>
              <td className="px-3 py-2 font-mono text-xs text-neutral-600">{row.lot_no}</td>
              <td className="px-3 py-2 text-right font-mono text-xs text-neutral-800">{formatQty(Number(row.qty))}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">{formatDate(row.count_date)}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">{row.expiry_date ? formatDate(row.expiry_date) : '—'}</td>
              <td className="px-3 py-2">{expirySourceBadge(row.expiry_source)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
