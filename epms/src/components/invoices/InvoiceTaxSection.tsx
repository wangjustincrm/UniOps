/**
 * Invoice tax lines editor (Phase a A2) — split the PRE-TAX amount into
 * taxable bases per tax code; the tax per line is computed (base × rate)
 * and stays editable for penny-rounding against the vendor's invoice.
 * Saving replaces the full set; header tax/total are derived server-side.
 * Tax codes come from the B2 tax engine (mdm-api).
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Loader2, Percent, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount } from '@/lib/utils'
import {
  getTaxCodes, getTaxLines, saveTaxLines, type TaxLine,
} from '@/services/invoiceTax'

const round2 = (n: number) => Math.round(n * 100) / 100

export function InvoiceTaxSection({ invoiceId, currency, pretaxAmount, headerTax, editable }: {
  invoiceId: string
  currency: string
  pretaxAmount: number
  headerTax: number
  editable: boolean
}) {
  const qc = useQueryClient()
  const [rows, setRows] = useState<TaxLine[]>([])
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: existing, isLoading } = useQuery({
    queryKey: ['invoice-tax-lines', invoiceId],
    queryFn: () => getTaxLines(invoiceId),
  })
  const { data: codes = [] } = useQuery({
    queryKey: ['tax-codes'],
    queryFn: getTaxCodes,
    staleTime: 10 * 60_000,
  })

  useEffect(() => {
    if (existing) { setRows(existing.lines); setDirty(false) }
  }, [existing])

  const save = useMutation({
    mutationFn: () => saveTaxLines(invoiceId, rows),
    onSuccess: () => {
      setError(null); setDirty(false)
      qc.invalidateQueries({ queryKey: ['invoice-tax-lines', invoiceId] })
      qc.invalidateQueries({ queryKey: ['invoice', invoiceId] })
      qc.invalidateQueries({ queryKey: ['invoices'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const rateOf = (code: string) => Number(codes.find((c) => c.code === code)?.rate ?? 0)

  const update = (i: number, patch: Partial<TaxLine>) => {
    setRows((r) => r.map((row, idx) => (idx === i ? { ...row, ...patch } : row)))
    setDirty(true)
  }
  /** base or code changed → recompute the tax for that line */
  const setBase = (i: number, base: string) => {
    const row = rows[i]
    update(i, {
      taxable_amount: base,
      tax_amount: row.tax_code
        ? round2(Number(base || 0) * rateOf(row.tax_code)).toFixed(2)
        : row.tax_amount,
    })
  }
  const pickCode = (i: number, code: string) => {
    const tc = codes.find((c) => c.code === code)
    const base = Number(rows[i].taxable_amount || 0)
    update(i, {
      tax_code: code,
      recoverable: tc ? tc.recoverable : true,
      tax_amount: round2(base * Number(tc?.rate ?? 0)).toFixed(2),
    })
  }
  const addRow = () => {
    // prefill the remaining unallocated pre-tax amount as the base
    const allocated = rows.reduce((s, r) => s + Number(r.taxable_amount || 0), 0)
    const remaining = Math.max(round2(pretaxAmount - allocated), 0)
    setRows((r) => [...r, {
      tax_code: '', taxable_amount: remaining.toFixed(2),
      tax_amount: '0.00', recoverable: true,
    }])
    setDirty(true)
  }
  const removeRow = (i: number) => {
    setRows((r) => r.filter((_, idx) => idx !== i))
    setDirty(true)
  }

  const baseTotal = rows.reduce((s, r) => s + Number(r.taxable_amount || 0), 0)
  const taxTotal = rows.reduce((s, r) => s + Number(r.tax_amount || 0), 0)
  const baseMismatch = rows.length > 0 && Math.abs(baseTotal - pretaxAmount) > 0.005
  const canSave = dirty && rows.every((r) => r.tax_code && Number(r.tax_amount) >= 0)

  return (
    <div className="rounded-lg border border-neutral-200 bg-white">
      <div className="flex items-center justify-between border-b border-neutral-100 px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          <Percent className="h-3.5 w-3.5" />
          Tax Lines
          <span className="normal-case font-normal text-neutral-300">
            (split the pre-tax amount by tax code — tax is computed per line; header tax updates on save)
          </span>
        </h3>
        {editable && (
          <Button size="sm" variant="secondary" onClick={addRow}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Add Line
          </Button>
        )}
      </div>

      <div className="p-4">
        {isLoading ? (
          <div className="flex justify-center py-4 text-neutral-300">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-neutral-400">
            No tax split yet — the header tax of {formatAmount(headerTax, currency)} will show
            as uncoded on the ITC return exception list.
            {editable && ' Click "Add Line" to allocate the pre-tax amount across tax codes.'}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-neutral-400">
              <span className="w-64">Tax code</span>
              <span className="w-32 text-right">Taxable base</span>
              <span className="w-28 text-right">Tax</span>
            </div>
            {rows.map((row, i) => (
              <div key={row.id ?? `new-${i}`} className="flex items-center gap-2 text-sm">
                <select value={row.tax_code} disabled={!editable}
                        onChange={(e) => pickCode(i, e.target.value)}
                        className="w-64 rounded-md border border-neutral-200 px-2 py-1.5 disabled:bg-neutral-50">
                  <option value="">— Tax code —</option>
                  {codes.map((c) => (
                    <option key={`${c.code}-${c.rate}`} value={c.code}>
                      {c.code} · {(Number(c.rate) * 100).toFixed(3).replace(/\.?0+$/, '')}%
                      {c.recoverable ? '' : ' (non-recoverable)'}
                    </option>
                  ))}
                </select>
                <input type="number" step="0.01" min="0"
                       value={row.taxable_amount ?? ''} disabled={!editable}
                       onChange={(e) => setBase(i, e.target.value)}
                       className="w-32 rounded-md border border-neutral-200 px-2 py-1.5 text-right disabled:bg-neutral-50" />
                <input type="number" step="0.01" min="0"
                       value={row.tax_amount} disabled={!editable}
                       title="Computed from base × rate — editable for rounding"
                       onChange={(e) => update(i, { tax_amount: e.target.value })}
                       className="w-28 rounded-md border border-neutral-200 px-2 py-1.5 text-right text-neutral-600 disabled:bg-neutral-50" />
                <label className={cn('flex items-center gap-1.5 text-xs',
                                     row.recoverable ? 'text-emerald-600' : 'text-neutral-400')}>
                  <input type="checkbox" checked={row.recoverable} disabled={!editable}
                         onChange={(e) => update(i, { recoverable: e.target.checked })} />
                  ITC recoverable
                </label>
                {editable && (
                  <button onClick={() => removeRow(i)}
                          className="text-neutral-300 hover:text-red-500">
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
            <div className="mt-1 flex items-center gap-4 border-t border-neutral-100 pt-2 text-sm">
              <span className="text-neutral-500">Base total</span>
              <span className={cn('font-medium', baseMismatch && 'text-amber-600')}>
                {formatAmount(baseTotal, currency)}
              </span>
              {baseMismatch && (
                <span className="flex items-center gap-1 text-xs text-amber-600">
                  <AlertCircle className="h-3.5 w-3.5" />
                  pre-tax amount is {formatAmount(pretaxAmount, currency)} — allocate the difference
                </span>
              )}
              <span className="ml-4 text-neutral-500">Tax total</span>
              <span className="font-medium">{formatAmount(taxTotal, currency)}</span>
            </div>
          </div>
        )}

        {error && (
          <div className="mt-3 flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}

        {editable && dirty && (
          <div className="mt-3 flex justify-end gap-2">
            <Button size="sm" variant="secondary"
                    onClick={() => { setRows(existing?.lines ?? []); setDirty(false); setError(null) }}>
              Reset
            </Button>
            <Button size="sm" disabled={!canSave || save.isPending}
                    onClick={() => save.mutate()}>
              {save.isPending && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
              Save Tax Lines
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
