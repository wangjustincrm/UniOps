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
import { AlertTriangle, ClipboardPaste, Loader2, Star, Trash2 } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { supplyApi, parsePaste, type MaterialSupplier, type BulkResult } from './supplyApi'

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

  const listQuery = useQuery({
    queryKey: ['material-suppliers'],
    queryFn: () => supplyApi.list(),
  })
  const rows = useMemo(() => listQuery.data?.items ?? [], [listQuery.data])

  const missingLeadTime = rows.filter((r) => r.lead_time_days === null).length
  const materialsWithoutPrimary = useMemo(() => {
    const byMaterial = new Map<string, boolean>()
    for (const r of rows) {
      byMaterial.set(r.material_code, (byMaterial.get(r.material_code) ?? false) || r.is_primary)
    }
    return [...byMaterial.values()].filter((hasPrimary) => !hasPrimary).length
  }, [rows])

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
          <Button type="button" size="sm" className="min-h-[44px]"
            onClick={() => { setResult(null); setPasteOpen(true) }}>
            <ClipboardPaste className="h-3.5 w-3.5" /> Paste from Excel
          </Button>
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
                No supply parameters yet — paste them from your spreadsheet to get started.
              </td></tr>
            )}
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-neutral-100">
                <td className="px-3 py-2 font-mono text-xs">{row.material_code}</td>
                <td className="px-3 py-2 font-mono text-xs">{row.partner_code}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {row.lead_time_days === null
                    ? <span className="text-danger-600">missing</span>
                    : `${row.lead_time_days} d`}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">{row.moq ?? '—'}</td>
                <td className="px-3 py-2 text-right font-mono text-neutral-600">{row.order_multiple ?? '—'}</td>
                <td className="px-3 py-2 text-center">
                  {row.is_primary && <Star aria-label="Primary supplier" className="mx-auto h-3.5 w-3.5 text-warning-500" />}
                </td>
                <td className="px-3 py-2 text-right">
                  {canWrite && (
                    <button type="button" onClick={() => setDeleteTarget(row)}
                      aria-label={`Remove ${row.material_code} / ${row.partner_code}`}
                      className="text-neutral-400 hover:text-danger-600">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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
