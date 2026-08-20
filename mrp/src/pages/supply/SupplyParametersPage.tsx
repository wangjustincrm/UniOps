// Supply Parameters — lead time, minimum order quantity and order multiple
// per material and supplier.
//
// Phase 1C's purchase suggestions work backwards from the lead time to get
// an order-by date, and raise a requirement to the MOQ. Until this page
// existed the table was hand-maintained with no interface at all: the rows
// could only be created by calling mdm-api directly, so in practice it was
// empty and every suggestion would have carried a "lead time missing"
// warning. A number nobody can enter is a number the plan cannot use.
//
// Bulk paste is the primary path, not a nicety: purchasing keeps these in a
// spreadsheet, and retyping a few hundred rows one dialog at a time is how
// the data stays in the spreadsheet forever.
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ClipboardPaste, Loader2, Pencil, Plus, Search, Star, Trash2, X } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { materialsApi } from '@/lib/materials'
import { supplyApi, parsePaste, type MaterialSupplier, type BulkResult } from './supplyApi'
import { SupplyRowDrawer } from './SupplyRowDrawer'

/** Numeric(18,4) comes back as '1000.0000'. Four decimal places on an order
 *  quantity is noise, and noise in a column people scan is a column they stop
 *  reading. */
function trimZeros(value: string | null): string {
  if (value === null) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? String(n) : value
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

const PASTE_COLUMNS = 'Material · Supplier · Lead time (days) · MOQ · Order multiple · Primary (yes/no)'

export default function SupplyParametersPage() {
  const toasts = useToasts()
  const queryClient = useQueryClient()
  const permsQuery = usePermissions()
  const canWrite = !!permsQuery.data?.permissions['mrp.param.write']

  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const [result, setResult] = useState<BulkResult | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<MaterialSupplier | null>(null)
  // null = closed; { row: null } = adding; { row } = editing that row.
  const [drawer, setDrawer] = useState<{ row: MaterialSupplier | null } | null>(null)

  const [query, setQuery] = useState('')

  const listQuery = useQuery({
    queryKey: ['material-suppliers'],
    queryFn: () => supplyApi.listAll(),
  })

  // Supplier names, so the list says who "001" is and the search box matches
  // the name people actually use when they talk about a supplier.
  const supplierNamesQuery = useQuery({
    queryKey: ['supplier-names'],
    queryFn: () => supplyApi.supplierNames(),
  })
  // Memoised: a fresh Map on every render would re-run the filter below on
  // every keystroke's re-render, not just when something actually changed.
  const supplierNames = useMemo(
    () => supplierNamesQuery.data ?? new Map<string, string>(),
    [supplierNamesQuery.data],
  )

  // The material master, for the unit each quantity is expressed in. "MOQ
  // 1000" is unreadable without it — kilograms and cans are both plausible
  // and the difference is three orders of magnitude.
  const materialsQuery = useQuery({
    queryKey: ['materials-all'],
    queryFn: () => materialsApi.listAll(),
  })
  const uomByCode = useMemo(() => {
    const m = new Map<string, string | null>()
    for (const mat of materialsQuery.data ?? []) m.set(mat.code, mat.base_uom)
    return m
  }, [materialsQuery.data])

  const materialNameByCode = useMemo(() => {
    const m = new Map<string, string>()
    for (const mat of materialsQuery.data ?? []) m.set(mat.code, mat.name ?? '')
    return m
  }, [materialsQuery.data])
  const allRows = useMemo(() => listQuery.data ?? [], [listQuery.data])

  // Matches material code, material name, supplier code and supplier name —
  // people look a row up by whichever of the four they happen to have.
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return allRows
    return allRows.filter((r) => {
      const haystack = [
        r.material_code,
        materialNameByCode.get(r.material_code) ?? '',
        r.partner_code,
        supplierNames.get(r.partner_code) ?? '',
      ].join(' ').toLowerCase()
      return haystack.includes(needle)
    })
  }, [allRows, query, materialNameByCode, supplierNames])

  // Counted over EVERY row, not the filtered view: "3 rows have no lead time"
  // must not change because somebody typed in the search box.
  const missingLeadTime = allRows.filter((r) => r.lead_time_days === null).length
  const materialsWithoutPrimary = useMemo(() => {
    const byMaterial = new Map<string, boolean>()
    for (const r of allRows) {
      byMaterial.set(r.material_code, (byMaterial.get(r.material_code) ?? false) || r.is_primary)
    }
    return [...byMaterial.values()].filter((hasPrimary) => !hasPrimary).length
  }, [allRows])

  const bulkMutation = useMutation({
    mutationFn: async (text: string) => {
      const { rows: parsed } = parsePaste(text)
      if (parsed.length === 0) throw new ApiError('Nothing to import — paste rows first.', 400, null)
      return supplyApi.bulk(parsed)
    },
    onSuccess: async (res) => {
      setResult(res)
      await queryClient.invalidateQueries({ queryKey: ['material-suppliers'] })
      if (res.errors.length === 0) {
        toasts.success(`Imported ${res.created} new and ${res.updated} updated row(s).`)
        setPasteOpen(false)
        setPasteText('')
      }
    },
    onError: (err) => toasts.error(errMsg(err, 'Could not import the pasted rows.')),
  })

  const deleteMutation = useMutation({
    mutationFn: (row: MaterialSupplier) => supplyApi.remove(row.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['material-suppliers'] })
      toasts.success('Removed.')
      setDeleteTarget(null)
    },
    onError: (err) => toasts.error(errMsg(err, 'Could not remove this row.')),
  })

  return (
    <div className="flex flex-col gap-4 p-4">
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />

      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-neutral-900">Supply Parameters</h1>
          <p className="text-xs text-neutral-500">
            Lead time and order sizing per material and supplier. Purchase suggestions work
            backwards from the lead time and raise requirements to the minimum order quantity.
          </p>
        </div>
        {canWrite && (
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" className="min-h-[44px]"
              onClick={() => setDrawer({ row: null })}>
              <Plus className="h-3.5 w-3.5" /> Add row
            </Button>
            <Button type="button" size="sm" variant="secondary" className="min-h-[44px]"
              onClick={() => { setResult(null); setPasteOpen(true) }}>
              <ClipboardPaste className="h-3.5 w-3.5" /> Paste from Excel
            </Button>
          </div>
        )}
      </header>

      {(missingLeadTime > 0 || materialsWithoutPrimary > 0) && (
        <p className="flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {missingLeadTime > 0 && (
              <>{missingLeadTime} row(s) have no lead time — those suggestions cannot say when
                to order. </>
            )}
            {materialsWithoutPrimary > 0 && (
              <>{materialsWithoutPrimary} material(s) have suppliers but none marked primary —
                purchase suggestions pick the primary one.</>
            )}
          </span>
        </p>
      )}

      {materialsQuery.isError && (
        <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0" />
          Could not load the material master — material names and the units on the quantities
          below are missing, and searching by material name will not match.
        </p>
      )}

      {supplierNamesQuery.isError && (
        // Without this the failure is invisible: names simply do not appear,
        // which reads as "these suppliers have no names on file" rather than
        // "the lookup failed". That is exactly how a wrong page size went
        // unnoticed.
        <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0" />
          Could not load supplier names — codes are shown on their own, and searching by
          supplier name will not match.
        </p>
      )}

      <div className="flex items-center gap-2">
        <div className="flex h-11 flex-1 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 sm:max-w-md">
          <Search aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search material or supplier — code or name…"
            aria-label="Search supply parameters"
            className="w-full text-sm focus:outline-none"
          />
          {query && (
            <button type="button" onClick={() => setQuery('')} aria-label="Clear search"
              className="shrink-0 text-neutral-400 hover:text-neutral-700">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <span className="text-xs text-neutral-500">
          {query
            ? `${rows.length} of ${allRows.length} row(s)`
            : `${allRows.length} row(s)`}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Material</th>
              <th className="px-3 py-2 text-left">Supplier</th>
              <th className="px-3 py-2 text-right">Lead time</th>
              <th className="px-3 py-2 text-right">MOQ</th>
              <th className="px-3 py-2 text-right">Order multiple</th>
              <th className="px-3 py-2 text-center">Primary</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>
            )}
            {!listQuery.isLoading && rows.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-neutral-400">
                {allRows.length === 0
                  ? 'No supply parameters yet — add a row, or paste them from your spreadsheet.'
                  : `Nothing matches "${query}".`}
              </td></tr>
            )}
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-neutral-100">
                <td className="px-3 py-2 text-xs">
                  <span className="font-mono">{row.material_code}</span>
                  {materialNameByCode.get(row.material_code) && (
                    <span className="ml-1.5 text-neutral-500">
                      {materialNameByCode.get(row.material_code)}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">
                  <span className="font-mono">{row.partner_code}</span>
                  {supplierNames.get(row.partner_code) && (
                    <span className="ml-1.5 text-neutral-500">
                      {supplierNames.get(row.partner_code)}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {row.lead_time_days === null
                    ? <span className="text-danger-600">missing</span>
                    : `${row.lead_time_days} d`}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">
                  {trimZeros(row.moq)}
                  {row.moq !== null && uomByCode.get(row.material_code) && (
                    <span className="ml-1 text-xs text-neutral-400">
                      {uomByCode.get(row.material_code)}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">
                  {trimZeros(row.order_multiple)}
                  {row.order_multiple !== null && uomByCode.get(row.material_code) && (
                    <span className="ml-1 text-xs text-neutral-400">
                      {uomByCode.get(row.material_code)}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  {row.is_primary && <Star aria-label="Primary supplier" className="mx-auto h-3.5 w-3.5 text-warning-500" />}
                </td>
                <td className="px-3 py-2 text-right">
                  {canWrite && (
                    <span className="inline-flex items-center gap-2">
                      <button type="button" onClick={() => setDrawer({ row })}
                        aria-label={`Edit ${row.material_code} / ${row.partner_code}`}
                        title="Edit"
                        className="text-neutral-400 hover:text-primary-600">
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button type="button" onClick={() => setDeleteTarget(row)}
                        aria-label={`Remove ${row.material_code} / ${row.partner_code}`}
                        title="Remove"
                        className="text-neutral-400 hover:text-danger-600">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {drawer && (
        <SupplyRowDrawer
          row={drawer.row}
          onClose={() => setDrawer(null)}
          onSaved={async (message) => {
            setDrawer(null)
            await queryClient.invalidateQueries({ queryKey: ['material-suppliers'] })
            toasts.success(message)
          }}
          notifyError={(message) => toasts.error(message)}
        />
      )}

      {pasteOpen && (
        <ConfirmDialog
          title="Paste supply parameters"
          confirmLabel="Import"
          busy={bulkMutation.isPending}
          onCancel={() => { setPasteOpen(false); setResult(null) }}
          onConfirm={() => bulkMutation.mutate(pasteText)}
        >
          <div className="flex flex-col gap-2">
            <p className="text-xs text-neutral-500">
              Copy the columns straight out of Excel, in this order:<br />
              <span className="font-medium text-neutral-700">{PASTE_COLUMNS}</span><br />
              A header row is skipped automatically. Existing rows for the same material and
              supplier are updated; a blank cell leaves that value as it is.
            </p>
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              rows={8}
              className="w-full rounded-lg border border-neutral-300 p-2 font-mono text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              placeholder={'CR0001\tSUP-A\t30\t500\t\tyes'}
            />
            {bulkMutation.isPending && (
              <p className="flex items-center gap-1.5 text-xs text-neutral-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Importing…
              </p>
            )}
            {result && result.errors.length > 0 && (
              // Good rows were saved; only the listed ones were not. Saying so
              // matters — otherwise the planner re-pastes everything and
              // wonders why the counts look wrong.
              <div className="rounded-md border border-danger-200 bg-danger-50 p-2 text-xs text-danger-700">
                <p className="font-medium">
                  {result.created + result.updated} row(s) saved, {result.errors.length} could not be:
                </p>
                <ul className="mt-1 list-inside list-disc">
                  {result.errors.slice(0, 10).map((e) => (
                    <li key={`${e.row}-${e.material_code}`}>
                      Row {e.row} ({e.material_code || '—'} / {e.partner_code || '—'}): {e.message}
                    </li>
                  ))}
                </ul>
                {result.errors.length > 10 && <p className="mt-1">…and {result.errors.length - 10} more.</p>}
              </div>
            )}
          </div>
        </ConfirmDialog>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Remove this supplier row?"
          confirmLabel="Remove"
          danger
          busy={deleteMutation.isPending}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => deleteMutation.mutate(deleteTarget)}
        >
          {deleteTarget.material_code} / {deleteTarget.partner_code} will no longer be considered
          when purchase suggestions pick a supplier.
        </ConfirmDialog>
      )}
    </div>
  )
}
