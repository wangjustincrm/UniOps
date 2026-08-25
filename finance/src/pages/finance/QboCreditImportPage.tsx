/**
 * QBO Vendor Credit Import (Finance) — a THROWAWAY migration screen.
 *
 * QuickBooks is being decommissioned. This maps the QBO vendors that still
 * hold an unapplied credit balance onto EPMS suppliers and brings those
 * balances into the vendor credit ledger. Delete this file, the route and
 * services/qboCreditImport.ts once that has happened.
 *
 * The one rule the UI enforces: a suggestion is a PRE-FILL, never a decision.
 * There is deliberately no "accept all suggestions" button — that would
 * reintroduce automatic mapping through the back door, and the measured data
 * already contains a vendor whose obvious guess was wrong.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Loader2, Search } from 'lucide-react'
import {
  qboCreditImportApi,
  type ImportRunResponse,
  type VendorOption,
} from '@/services/qboCreditImport'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { cn } from '@/lib/utils'

const STORAGE_KEY = 'qbo-credit-import-decisions'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const inputCls = 'h-9 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

/** chosen vendor id per QBO vendor, plus rows the operator marked skip. */
interface Decisions {
  chosen: Record<string, string>
  skipped: string[]
}

const EMPTY: Decisions = { chosen: {}, skipped: [] }

/** localStorage can throw outright (private windows, blocked site data), so
 * every read and write is guarded and the page renders fine with nothing
 * stored. Decisions live here rather than in the database because this tool is
 * disposable and must leave no schema behind. */
function loadDecisions(): Decisions {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<Decisions>
    return {
      chosen: parsed.chosen && typeof parsed.chosen === 'object' ? parsed.chosen : {},
      skipped: Array.isArray(parsed.skipped) ? parsed.skipped : [],
    }
  } catch {
    return EMPTY
  }
}

function saveDecisions(d: Decisions) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(d))
  } catch {
    /* a lost convenience, not a lost import — the mapping is sent explicitly */
  }
}

function money(v: string): string {
  const n = Number(v)
  return Number.isNaN(n) ? v : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function QboCreditImportPage() {
  const [decisions, setDecisions] = useState<Decisions>(loadDecisions)
  // A Preview must have run in THIS session before Import unlocks, so nobody
  // commits a mapping they have not seen the consequences of.
  const [preview, setPreview] = useState<ImportRunResponse | null>(null)
  const [result, setResult] = useState<ImportRunResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { saveDecisions(decisions) }, [decisions])

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['qbo-credit-import-candidates'],
    queryFn: qboCreditImportApi.candidates,
  })

  const candidates = data?.candidates ?? []
  const drift = data?.drift ?? []

  const mapping = useMemo(() => {
    const out: Record<string, string> = {}
    for (const c of candidates) {
      if (decisions.skipped.includes(c.qbo_vendor_id)) continue
      const v = decisions.chosen[c.qbo_vendor_id]
      if (v) out[c.qbo_vendor_id] = v
    }
    return out
  }, [candidates, decisions])

  const mappedCount = Object.keys(mapping).length

  const run = useMutation({
    mutationFn: (dryRun: boolean) => qboCreditImportApi.run(mapping, dryRun),
    onSuccess: (res) => {
      setError(null)
      if (res.dry_run) {
        setPreview(res)
        setResult(null)
      } else {
        setResult(res)
        setPreview(null)
        void refetch()
      }
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Request failed'),
  })

  function choose(qboVendorId: string, vendorId: string | null) {
    // Any change invalidates the preview the operator was about to commit.
    setPreview(null)
    setDecisions((d) => {
      const chosen = { ...d.chosen }
      if (vendorId) chosen[qboVendorId] = vendorId
      else delete chosen[qboVendorId]
      return { ...d, chosen }
    })
  }

  function toggleSkip(qboVendorId: string) {
    setPreview(null)
    setDecisions((d) => ({
      ...d,
      skipped: d.skipped.includes(qboVendorId)
        ? d.skipped.filter((s) => s !== qboVendorId)
        : [...d.skipped, qboVendorId],
    }))
  }

  return (
    <PortalChromeLayout
      title="QuickBooks Vendor Credit Import"
      subtitle="Bring the vendor credit balances QuickBooks still holds into UniOps."
    >
      <div className="space-y-5">
        <section className="space-y-2 rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-sm text-neutral-600">
          <p>
            Only credits QuickBooks still shows an <strong>unapplied balance</strong> on
            are listed. Credits QuickBooks already applied in full are history, not
            balances — they stay readable on the QuickBooks Mirror page and are not
            imported.
          </p>
          <p>
            A credit only ever reduces a payment in <strong>the same currency</strong>.
            A USD credit will not absorb a CAD payment.
          </p>
          <p>
            Where no EPMS vendor is suggested, create the vendor in Vendor Master and
            reload this page — it will match automatically on the next run.
          </p>
          {data?.cutover.finished_at && (
            <p className="text-xs text-neutral-500">
              Reading the QuickBooks full reload finished{' '}
              {new Date(data.cutover.finished_at).toLocaleString()}. Run a fresh full
              reload first if these balances look stale.
            </p>
          )}
        </section>

        {drift.length > 0 && (
          <section className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-amber-900">
              <AlertTriangle className="h-4 w-4" /> Changed inside QuickBooks after import
            </h2>
            <p className="text-xs text-amber-800">
              These credits were imported already, and QuickBooks has since reported a
              different balance. Neither side has been altered — decide which is right
              and correct it by hand. After cutover, vendor credits should be applied
              only in UniOps; two ledgers decrementing independently will double-spend.
            </p>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-amber-900">
                <tr>
                  <th className="py-1 pr-3 font-medium">Credit</th>
                  <th className="py-1 pr-3 font-medium">Vendor</th>
                  <th className="py-1 pr-3 text-right font-medium">Imported</th>
                  <th className="py-1 pr-3 text-right font-medium">QuickBooks now</th>
                  <th className="py-1 text-right font-medium">Applied here</th>
                </tr>
              </thead>
              <tbody>
                {drift.map((d) => (
                  <tr key={d.source_ref} className="border-t border-amber-200">
                    <td className="py-1 pr-3 font-mono text-xs">{d.credit_number}</td>
                    <td className="py-1 pr-3">{d.vendor_name}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(d.imported_total)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(d.qbo_balance)}</td>
                    <td className="py-1 text-right tabular-nums">{money(d.applied_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <section className="overflow-auto rounded-lg border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-neutral-500">
              <tr>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">QuickBooks vendor</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 text-right font-medium">Credits</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 text-right font-medium">Open balance</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">Currency</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">EPMS vendor</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
              )}
              {!isLoading && candidates.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                  No QuickBooks vendor credits with an open balance.</td></tr>
              )}
              {candidates.map((c) => {
                const skipped = decisions.skipped.includes(c.qbo_vendor_id)
                const chosen = decisions.chosen[c.qbo_vendor_id] ?? null
                return (
                  <tr key={c.qbo_vendor_id} className={cn('border-t border-neutral-100', skipped && 'opacity-50')}>
                    <td className="px-3 py-2">{c.qbo_display_name ?? '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{c.credit_count}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{money(c.credit_total)}</td>
                    <td className="px-3 py-2 text-xs text-neutral-600">{c.currencies.join(', ')}</td>
                    <td className="px-3 py-2">
                      <VendorPicker
                        suggestedId={c.suggested_vendor_id}
                        suggestedName={c.suggested_vendor_name}
                        chosenId={chosen}
                        disabled={skipped}
                        onChoose={(id) => choose(c.qbo_vendor_id, id)}
                      />
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <div className="flex flex-col gap-1">
                        {c.already_imported > 0 && (
                          <span className="text-neutral-500">
                            {c.already_imported} already imported
                          </span>
                        )}
                        <button
                          type="button"
                          className="self-start text-neutral-500 underline hover:text-neutral-700"
                          onClick={() => toggleSkip(c.qbo_vendor_id)}
                        >
                          {skipped ? 'Include' : 'Skip'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>

        <section className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={secondaryBtn}
            disabled={run.isPending || mappedCount === 0}
            onClick={() => { if (!run.isPending) run.mutate(true) }}
          >
            {run.isPending && run.variables === true
              ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Preview {mappedCount > 0 ? `(${mappedCount} vendors)` : ''}
          </button>
          <button
            type="button"
            className={primaryBtn}
            // Import unlocks only after a preview of the CURRENT mapping.
            disabled={run.isPending || !preview || preview.imported === 0}
            onClick={() => { if (!run.isPending) run.mutate(false) }}
          >
            {run.isPending && run.variables === false
              ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Import
          </button>
          {mappedCount === 0 && (
            <span className="text-sm text-neutral-500">
              Confirm at least one EPMS vendor to continue.
            </span>
          )}
        </section>

        {error && (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        )}

        {preview && <RunSummary run={preview} />}
        {result && <RunSummary run={result} />}
      </div>
    </PortalChromeLayout>
  )
}

function RunSummary({ run }: { run: ImportRunResponse }) {
  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-neutral-800">
        {run.dry_run ? 'Preview — nothing has been written' : 'Import complete'}
      </h2>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-neutral-700">
        <span><strong>{run.imported}</strong> {run.dry_run ? 'would be imported' : 'imported'}</span>
        <span>total <strong className="tabular-nums">{money(run.total_amount)}</strong></span>
        <span>{run.skipped_unmapped} skipped (no EPMS vendor)</span>
        <span>{run.skipped_existing} already imported</span>
        <span>{run.skipped_duplicate} already in the ledger</span>
      </div>

      {run.duplicates.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="mb-1 font-medium">
            Already recorded from a manual upload — skipped so the vendor&apos;s credit is
            not doubled:
          </p>
          <ul className="space-y-0.5">
            {run.duplicates.map((d) => (
              <li key={d.qbo_id} className="font-mono">
                {d.vendor_credit_number} → {d.existing_credit_number}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.rows.length > 0 && (
        <div className="max-h-72 overflow-auto rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="sticky top-0 text-left text-xs text-neutral-500">
              <tr>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">Credit note</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">Vendor</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 text-right font-medium">Amount</th>
                <th className="border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">Currency</th>
              </tr>
            </thead>
            <tbody>
              {run.rows.map((r) => (
                <tr key={r.qbo_id} className="border-t border-neutral-100">
                  <td className="px-3 py-1.5 font-mono text-xs">{r.vendor_credit_number}</td>
                  <td className="px-3 py-1.5">{r.vendor_name}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{money(r.amount)}</td>
                  <td className="px-3 py-1.5 text-xs text-neutral-600">{r.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/**
 * Vendor cell. A backend suggestion is shown as a proposal the operator must
 * accept — accepting is what puts the vendor into `chosen`, which is the only
 * thing that reaches the import.
 */
function VendorPicker({
  suggestedId, suggestedName, chosenId, disabled, onChoose,
}: {
  suggestedId: string | null
  suggestedName: string | null
  chosenId: string | null
  disabled: boolean
  onChoose: (id: string | null) => void
}) {
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)

  const { data } = useQuery({
    queryKey: ['qbo-credit-import-vendors', search],
    queryFn: () => qboCreditImportApi.vendors(search || undefined),
    enabled: open,
  })
  const options: VendorOption[] = data?.items ?? []

  const chosenName = options.find((o) => o.id === chosenId)?.name
    ?? (chosenId && chosenId === suggestedId ? suggestedName : null)

  if (disabled) return <span className="text-xs text-neutral-400">Skipped</span>

  if (chosenId) {
    return (
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800">
          <Check className="h-3 w-3" /> {chosenName ?? 'Selected'}
        </span>
        <button type="button" className="text-xs text-neutral-500 underline hover:text-neutral-700"
          onClick={() => { onChoose(null); setOpen(false) }}>
          Change
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {suggestedId && !open && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-neutral-600">
            Suggested: <span className="font-medium">{suggestedName}</span>
          </span>
          <button type="button" className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800 hover:bg-emerald-100"
            onClick={() => onChoose(suggestedId)}>
            Confirm
          </button>
          <button type="button" className="text-xs text-neutral-500 underline hover:text-neutral-700"
            onClick={() => setOpen(true)}>
            Pick another
          </button>
        </div>
      )}

      {!suggestedId && !open && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-neutral-500">
            No EPMS vendor matched — create it in Vendor Master, or pick one.
          </span>
          <button type="button" className="text-xs text-neutral-600 underline hover:text-neutral-800"
            onClick={() => setOpen(true)}>
            Pick
          </button>
        </div>
      )}

      {open && (
        <div className="space-y-1">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-neutral-400" />
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search EPMS vendors"
              className={cn(inputCls, 'pl-8')}
            />
          </div>
          <div className="max-h-40 overflow-auto rounded-lg border border-neutral-200">
            {options.length === 0 && (
              <p className="px-2 py-2 text-xs text-neutral-400">No matches.</p>
            )}
            {options.map((o) => (
              <button
                key={o.id}
                type="button"
                className="block w-full px-2 py-1.5 text-left text-xs hover:bg-neutral-50"
                onClick={() => { onChoose(o.id); setOpen(false) }}
              >
                <span className="font-medium">{o.name}</span>
                <span className="ml-2 text-neutral-400">{o.code}</span>
              </button>
            ))}
          </div>
          <button type="button" className="text-xs text-neutral-500 underline hover:text-neutral-700"
            onClick={() => setOpen(false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
