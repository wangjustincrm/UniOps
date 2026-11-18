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
import { Button, Input, MenuItem, SortTh, TBody, THead, Table, Td, Th, Tr, useSortableRows } from '@uniops/shell'
import {
  qboCreditImportApi,
  type ImportRunResponse,
  type VendorOption,
} from '@/services/qboCreditImport'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { cn, formatDateTime } from '@/lib/utils'

const STORAGE_KEY = 'qbo-credit-import-decisions'

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
  // Both loaded whole. Rows are keyed by QBO vendor id and the decisions
  // (chosen vendor / skipped) are kept by that id, so re-sorting is safe. The
  // EPMS vendor column is a picker (an input), so it is not a sort column.
  const driftSort = useSortableRows(drift, {
    credit: (d) => d.credit_number, vendor: (d) => d.vendor_name,
    imported: (d) => Number(d.imported_total), qbo: (d) => Number(d.qbo_balance),
    applied: (d) => Number(d.applied_amount),
  })
  const candSort = useSortableRows(candidates, {
    vendor: (c) => c.qbo_display_name, credits: (c) => c.credit_count,
    balance: (c) => Number(c.credit_total), currency: (c) => c.currencies.join(', '),
    status: (c) => c.already_imported || null,
  })

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
        <section className="space-y-2 rounded-lg border border-line bg-surface-subtle p-4 text-sm text-fg-secondary">
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
            <p className="text-xs text-fg-muted">
              Reading the QuickBooks full reload finished{' '}
              {formatDateTime(data.cutover.finished_at)}. Run a fresh full
              reload first if these balances look stale.
            </p>
          )}
        </section>

        {drift.length > 0 && (
          <section className="space-y-2 rounded-lg border border-warning-line bg-warning-soft p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-warning-fg">
              <AlertTriangle className="h-4 w-4" /> Changed inside QuickBooks after import
            </h2>
            <p className="text-xs text-warning-fg">
              These credits were imported already, and QuickBooks has since reported a
              different balance. Neither side has been altered — decide which is right
              and correct it by hand. After cutover, vendor credits should be applied
              only in UniOps; two ledgers decrementing independently will double-spend.
            </p>
            <Table density="compact">
              <THead>
                <Tr>
                  <SortTh label="Credit" sortKey="credit" sort={driftSort.sort} onSort={driftSort.onSort} className="py-1 pr-3 font-medium" />
                  <SortTh label="Vendor" sortKey="vendor" sort={driftSort.sort} onSort={driftSort.onSort} className="py-1 pr-3 font-medium" />
                  <SortTh label="Imported" sortKey="imported" sort={driftSort.sort} onSort={driftSort.onSort} align="right" className="py-1 pr-3 font-medium" />
                  <SortTh label="QuickBooks now" sortKey="qbo" sort={driftSort.sort} onSort={driftSort.onSort} align="right" className="py-1 pr-3 font-medium" />
                  <SortTh label="Applied here" sortKey="applied" sort={driftSort.sort} onSort={driftSort.onSort} align="right" className="py-1 font-medium" />
                </Tr>
              </THead>
              <TBody>
                {driftSort.rows.map((d) => (
                  <Tr key={d.source_ref} className="border-warning-line">
                    <Td className="pr-3 font-mono text-xs">{d.credit_number}</Td>
                    <Td className="pr-3">{d.vendor_name}</Td>
                    <Td align="right" className="pr-3 tabular-nums">{money(d.imported_total)}</Td>
                    <Td align="right" className="pr-3 tabular-nums">{money(d.qbo_balance)}</Td>
                    <Td align="right" className="tabular-nums">{money(d.applied_amount)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </section>
        )}

        <section className="overflow-auto rounded-lg border border-line bg-surface">
          <Table density="compact">
            <THead>
              <Tr>
                <SortTh label="QuickBooks vendor" sortKey="vendor" sort={candSort.sort} onSort={candSort.onSort} className="border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
                <SortTh label="Credits" sortKey="credits" sort={candSort.sort} onSort={candSort.onSort} align="right" className="border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
                <SortTh label="Open balance" sortKey="balance" sort={candSort.sort} onSort={candSort.onSort} align="right" className="border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
                <SortTh label="Currency" sortKey="currency" sort={candSort.sort} onSort={candSort.onSort} className="border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
                <Th className="border-b border-line bg-surface-subtle">EPMS vendor</Th>
                <SortTh label="Status" sortKey="status" sort={candSort.sort} onSort={candSort.onSort} className="border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
              </Tr>
            </THead>
            <TBody>
              {isLoading && (
                <Tr><Td align="center" colSpan={6} className="text-fg-muted">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" /></Td></Tr>
              )}
              {!isLoading && candidates.length === 0 && (
                <Tr><Td align="center" colSpan={6} className="text-fg-muted">
                  No QuickBooks vendor credits with an open balance.</Td></Tr>
              )}
              {candSort.rows.map((c) => {
                const skipped = decisions.skipped.includes(c.qbo_vendor_id)
                const chosen = decisions.chosen[c.qbo_vendor_id] ?? null
                return (
                  <Tr key={c.qbo_vendor_id} className={cn('border-t border-line-subtle', skipped && 'opacity-50')}>
                    <Td>{c.qbo_display_name ?? '—'}</Td>
                    <Td align="right" className="tabular-nums">{c.credit_count}</Td>
                    <Td align="right" className="tabular-nums">{money(c.credit_total)}</Td>
                    <Td className="text-xs text-fg-secondary">{c.currencies.join(', ')}</Td>
                    <Td>
                      <VendorPicker
                        suggestedId={c.suggested_vendor_id}
                        suggestedName={c.suggested_vendor_name}
                        chosenId={chosen}
                        disabled={skipped}
                        onChoose={(id) => choose(c.qbo_vendor_id, id)}
                      />
                    </Td>
                    <Td className="text-xs">
                      <div className="flex flex-col gap-1">
                        {c.already_imported > 0 && (
                          <span className="text-fg-muted">
                            {c.already_imported} already imported
                          </span>
                        )}
                        <Button variant="link"
                          type="button"
                          className="self-start  text-fg-muted"
                          onClick={() => toggleSkip(c.qbo_vendor_id)}
                        >
                          {skipped ? 'Include' : 'Skip'}
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        </section>

        <section className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" size="md"
            type="button"
            
            disabled={run.isPending || mappedCount === 0}
            onClick={() => { if (!run.isPending) run.mutate(true) }}
          >
            {run.isPending && run.variables === true
              ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Preview {mappedCount > 0 ? `(${mappedCount} vendors)` : ''}
          </Button>
          <Button size="md"
            type="button"
            
            // Import unlocks only after a preview of the CURRENT mapping.
            disabled={run.isPending || !preview || preview.imported === 0}
            onClick={() => { if (!run.isPending) run.mutate(false) }}
          >
            {run.isPending && run.variables === false
              ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Import
          </Button>
          {mappedCount === 0 && (
            <span className="text-sm text-fg-muted">
              Confirm at least one EPMS vendor to continue.
            </span>
          )}
        </section>

        {error && (
          <p className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>
        )}

        {preview && <RunSummary run={preview} />}
        {result && <RunSummary run={result} />}
      </div>
    </PortalChromeLayout>
  )
}

function RunSummary({ run }: { run: ImportRunResponse }) {
  return (
    <section className="space-y-3 rounded-lg border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">
        {run.dry_run ? 'Preview — nothing has been written' : 'Import complete'}
      </h2>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-fg-secondary">
        <span><strong>{run.imported}</strong> {run.dry_run ? 'would be imported' : 'imported'}</span>
        <span>total <strong className="tabular-nums">{money(run.total_amount)}</strong></span>
        <span>{run.skipped_unmapped} skipped (no EPMS vendor)</span>
        <span>{run.skipped_existing} already imported</span>
        <span>{run.skipped_duplicate} already in the ledger</span>
      </div>

      {run.duplicates.length > 0 && (
        <div className="rounded-lg border border-warning-line bg-warning-soft p-3 text-xs text-warning-fg">
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
        <div className="max-h-72 overflow-auto rounded-lg border border-line">
          <Table density="compact">
            <THead className="sticky top-0">
              <Tr>
                <Th className="border-b border-line bg-surface-subtle">Credit note</Th>
                <Th className="border-b border-line bg-surface-subtle">Vendor</Th>
                <Th align="right" className="border-b border-line bg-surface-subtle ">Amount</Th>
                <Th className="border-b border-line bg-surface-subtle">Currency</Th>
              </Tr>
            </THead>
            <TBody>
              {run.rows.map((r) => (
                <Tr key={r.qbo_id}>
                  <Td className="font-mono text-xs">{r.vendor_credit_number}</Td>
                  <Td>{r.vendor_name}</Td>
                  <Td align="right" className="tabular-nums">{money(r.amount)}</Td>
                  <Td className="text-xs text-fg-secondary">{r.currency}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
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

  if (disabled) return <span className="text-xs text-fg-muted">Skipped</span>

  if (chosenId) {
    return (
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2 py-0.5 text-xs text-success-fg">
          <Check className="h-3 w-3" /> {chosenName ?? 'Selected'}
        </span>
        <Button variant="link" type="button" className="text-xs text-fg-muted"
          onClick={() => { onChoose(null); setOpen(false) }}>
          Change
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {suggestedId && !open && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-fg-secondary">
            Suggested: <span className="font-medium">{suggestedName}</span>
          </span>
          <Button variant="secondary" size="xs" type="button"
            onClick={() => onChoose(suggestedId)}>
            Confirm
          </Button>
          <Button variant="link" type="button" className="text-xs text-fg-muted"
            onClick={() => setOpen(true)}>
            Pick another
          </Button>
        </div>
      )}

      {!suggestedId && !open && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-fg-muted">
            No EPMS vendor matched — create it in Vendor Master, or pick one.
          </span>
          <Button variant="link" type="button" className="text-xs"
            onClick={() => setOpen(true)}>
            Pick
          </Button>
        </div>
      )}

      {open && (
        <div className="space-y-1">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-fg-muted" />
            <Input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search EPMS vendors"
              className="pl-8"
            />
          </div>
          <div className="max-h-40 overflow-auto rounded-lg border border-line">
            {options.length === 0 && (
              <p className="px-2 py-2 text-xs text-fg-muted">No matches.</p>
            )}
            {options.map((o) => (
              <MenuItem
                key={o.id}
                className="px-2 py-1.5 text-xs"
                onClick={() => { onChoose(o.id); setOpen(false) }}
              >
                <span className="font-medium">{o.name}</span>
                <span className="ml-2 text-fg-muted">{o.code}</span>
              </MenuItem>
            ))}
          </div>
          <Button variant="link" type="button" className="text-xs text-fg-muted"
            onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  )
}
