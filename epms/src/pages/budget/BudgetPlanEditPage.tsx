/**
 * Budget Plan grid editor — month columns + Q/Y aggregates.
 *
 * Uses @tanstack/react-virtual to virtualize rows so the page scales to
 * 200+ accounts without performance issues. The grid is built with CSS grid
 * (not a native HTML table) so each virtualized row can be absolutely
 * positioned. Header is sticky-top above the scroll container.
 */
import { useState, useMemo, useRef, memo } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import {
  ArrowLeft, Layers, Lock, Send, RotateCcw, CheckCircle2, XCircle, Copy,
  GitBranch, History, X, Download, Upload,
} from 'lucide-react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import {
  usePlan, useUpdatePlanLine, usePlanAction, useCopyPlanFromPrev,
  useRevisePlan, usePlanVersions, useImportPlan,
} from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useTasks } from '@/hooks/useTasks'
import { useAuthStore } from '@/stores/auth.store'
import { budgetService, type ApiAccountPlanRow, type PlanImportResult } from '@/services/budget'
import type { DocumentStatus } from '@/types'
import { BreakdownMatrixModal } from './BreakdownMatrixModal'
import { ActionMessage, useConfirmDialog } from '@/components/ui/ConfirmDialog'

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]
const WRITE_ROLES = new Set(['system_admin', 'finance_manager', 'finance_bp', 'dept_manager'])

// CSS grid column template: [Account name | 12 × month | Q1-Q4 | Year]
const GRID_COLS = '280px repeat(12, minmax(90px, 1fr)) repeat(4, 100px) 110px'

const ROW_HEIGHT = 36
const HEADER_HEIGHT = 38
const L1_ROW_HEIGHT = 36

// ── Flat row model: account rows interleaved with L1 group headers ──────────

type FlatRow =
  | { kind: 'l1'; key: string; l1_code: string; l1_name: string }
  | { kind: 'account'; key: string; row: ApiAccountPlanRow }

export default function BudgetPlanEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const canEdit = !!user && WRITE_ROLES.has(user.role)
  const { data: planGrid, isLoading } = usePlan(id ?? null)
  const { data: ccData } = useCostCenters({ active_only: false })
  // Pre-fetch the full version chain at page level so we can detect any
  // existing draft revision and offer "Open Draft v{n}" before the user
  // hits a 409 from the backend.
  const versionsCcId  = planGrid?.plan.cost_center_id ?? null
  const versionsYear  = planGrid?.plan.fiscal_year ?? null
  const { data: allVersions } = usePlanVersions(versionsCcId, versionsYear)
  // Approval gating mirrors PoDetailPage: show Approve/Return/Reject only when
  // the current user holds an active approve_budget_plan task for THIS plan.
  // The approval step (e.g. finance_manager) is a Role Management assignment,
  // NOT a JWT role, so user.role can't be trusted here — the engine's task
  // routing is the single source of truth (and keeps the button in sync with
  // the inbox). See feedback: approval roles are assignments.
  const { data: myTasks } = useTasks({ is_completed: false })
  const planAction = usePlanAction()
  const copyPrev = useCopyPlanFromPrev()
  const reviseMut = useRevisePlan()
  const importMut = useImportPlan()
  const [breakdownCell, setBreakdownCell] = useState<{ row: ApiAccountPlanRow; month: number } | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [reviseOpen, setReviseOpen] = useState(false)
  const [reviseError, setReviseError] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const confirmDlg = useConfirmDialog()
  const [importResult, setImportResult] = useState<PlanImportResult | null>(null)
  const importInputRef = useRef<HTMLInputElement>(null)

  const parentRef = useRef<HTMLDivElement>(null)

  const ccLabel = useMemo(() => {
    if (!planGrid) return ''
    const cc = (ccData ?? []).find((c) => c.id === planGrid.plan.cost_center_id)
    return cc ? `${cc.code} — ${cc.name}` : planGrid.plan.cost_center_id
  }, [planGrid, ccData])

  // Tab title: `Plan CC001 FY2026` beats `Plan 3f9a20b1`. The cost-centre code
  // may resolve later than the plan (two independent queries), so this settles
  // in two steps rather than waiting for both.
  const ccCode = (ccData ?? []).find((c) => c.id === planGrid?.plan.cost_center_id)?.code
  useDocTabTitle(planGrid ? `Plan ${ccCode ? `${ccCode} ` : ''}FY${planGrid.plan.fiscal_year}` : undefined)

  // Build a flat row array — alternating L1 headers + account rows
  const flatRows: FlatRow[] = useMemo(() => {
    if (!planGrid) return []
    const groups = new Map<string, { l1_code: string; l1_name: string; rows: ApiAccountPlanRow[] }>()
    for (const r of planGrid.rows) {
      if (!groups.has(r.l1_code)) {
        groups.set(r.l1_code, { l1_code: r.l1_code, l1_name: r.l1_name, rows: [] })
      }
      groups.get(r.l1_code)!.rows.push(r)
    }
    const sortedGroups = Array.from(groups.values()).sort((a, b) => a.l1_code.localeCompare(b.l1_code))
    const out: FlatRow[] = []
    for (const g of sortedGroups) {
      out.push({ kind: 'l1', key: `l1-${g.l1_code}`, l1_code: g.l1_code, l1_name: g.l1_name })
      for (const r of g.rows) {
        out.push({ kind: 'account', key: r.account_id, row: r })
      }
    }
    return out
  }, [planGrid])

  // Virtualizer — variable row height (L1 headers same height as accounts here)
  const rowVirtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (idx) => flatRows[idx]?.kind === 'l1' ? L1_ROW_HEIGHT : ROW_HEIGHT,
    overscan: 10,
  })

  // Per-column totals across every account row in the grid. Memoized so the
  // sticky totals footer doesn't recompute on unrelated re-renders.
  const columnTotals = useMemo(() => {
    if (!planGrid) return null
    const months: Record<number, number> = {}
    for (let i = 1; i <= 12; i++) months[i] = 0
    let q1 = 0, q2 = 0, q3 = 0, q4 = 0, year = 0
    for (const r of planGrid.rows) {
      for (let i = 1; i <= 12; i++) {
        months[i] += Number(r.months[i] ?? 0)
      }
      q1 += Number(r.q1 ?? 0)
      q2 += Number(r.q2 ?? 0)
      q3 += Number(r.q3 ?? 0)
      q4 += Number(r.q4 ?? 0)
      year += Number(r.year_total ?? 0)
    }
    return { months, q1, q2, q3, q4, year }
  }, [planGrid])

  if (isLoading) return <div className="p-6 text-sm text-fg-muted">Loading plan…</div>
  if (!planGrid) {
    return (
      <div className="p-6 text-sm text-fg-muted">
        Plan not found. <BackLink to="/budget/plans" className="text-primary-600">← Back to list</BackLink>
      </div>
    )
  }

  const { plan, grand_total } = planGrid
  const isEditableStatus = plan.status === 'draft' || plan.status === 'returned'
  const editable = canEdit && isEditableStatus

  // Approve/Return/Reject visibility: an active approve_budget_plan task for this
  // plan means the engine routed the current step to this user (regardless of how
  // their approver role was assigned). system_admin holds every task too.
  const canApprove =
    (plan.status === 'submitted' || plan.status === 'in_review') &&
    (myTasks?.items ?? []).some(
      (t) => t.document_id === plan.id && t.type === 'approve_budget_plan',
    )

  // An existing non-terminal sibling version means a revision is already in
  // progress — block "Revise Plan" and surface a "Open Draft v{n}" affordance
  // pointing at the open draft so the user can find it.
  const _nonTerminal = new Set(['draft', 'submitted', 'in_review', 'returned'])
  const openRevision = (allVersions ?? []).find(
    (v) => v.id !== plan.id && _nonTerminal.has(v.status),
  ) ?? null

  // Asked in-page (were the browser's confirm); a refusal shows in the dialog.
  const handleAction = (action: 'submit' | 'approve' | 'return' | 'reject') => {
    const verb = { submit: 'submit', approve: 'approve', return: 'return', reject: 'reject' }[action]
    confirmDlg.ask({
      title: `${verb[0].toUpperCase()}${verb.slice(1)} Plan`,
      message: `Are you sure you want to ${verb} this plan?`,
      confirmLabel: `${verb[0].toUpperCase()}${verb.slice(1)}`,
      tone: action === 'reject' ? 'danger' : 'primary',
      onConfirm: () => planAction.mutateAsync({ planId: plan.id, action }),
    })
  }

  const handleCopyPrev = () => {
    confirmDlg.ask({
      title: `Copy FY ${plan.fiscal_year - 1}`,
      message: `Copy plan amounts from FY ${plan.fiscal_year - 1}? Overwrites current draft.`,
      confirmLabel: 'Copy',
      tone: 'primary',
      onConfirm: () => copyPrev.mutateAsync(plan.id),
    })
  }

  const handleExport = async () => {
    setExportError(null)
    try {
      await budgetService.exportPlan(plan.id)
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export failed')
    }
  }

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''  // allow re-uploading the same file
    if (!file) return
    importMut.mutate(
      { planId: plan.id, file },
      {
        onSuccess: (result) => {
          setImportResult(result)
          // auto-dismiss success after a while; keep error banners around
          if (result.errors.length === 0) {
            setTimeout(() => setImportResult(null), 6000)
          }
        },
        onError: (err) => {
          setImportResult({
            lines_updated: 0, accounts_touched: 0, accounts_skipped: 0, breakdowns_cleared: 0,
            errors: [err instanceof Error ? err.message : 'Import failed'],
          })
        },
      },
    )
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      {/* Top nav */}
      <div className="flex items-center gap-3">
        <BackLink to="/budget/plans">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
        </BackLink>
      </div>

      {/* Plan header card */}
      <div className="rounded-xl border border-line bg-surface p-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-bold text-fg">Budget Plan FY {plan.fiscal_year}</h1>
              <StatusBadge status={plan.status as DocumentStatus} />
              <span className="inline-flex items-center gap-1 rounded-md border border-line bg-surface-subtle px-2 py-0.5 text-2xs font-mono text-fg-secondary">
                <GitBranch className="h-3 w-3" />
                v{plan.version}
              </span>
              {plan.is_current ? (
                <span className="rounded-full bg-primary-50 text-primary-700 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide">
                  Current
                </span>
              ) : plan.status === 'approved' ? (
                <span className="rounded-full bg-surface-sunken text-fg-muted px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide">
                  Superseded
                </span>
              ) : null}
            </div>
            <p className="text-sm text-fg-secondary mt-1">Cost Center: <span className="font-medium">{ccLabel}</span></p>
            {plan.notes && <p className="text-xs text-fg-muted mt-1">Notes: {plan.notes}</p>}
            {plan.revision_notes && (
              <p className="text-xs text-fg-muted mt-1">
                <span className="font-semibold">Revision:</span> {plan.revision_notes}
              </p>
            )}
            <p className="text-xs text-fg-muted mt-1">
              Grand Total: <span className="font-mono font-semibold text-fg-secondary">{formatAmount(grand_total, 'CAD')}</span>
              {' '}· {flatRows.filter(r => r.kind === 'account').length} accounts
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="ghost" size="sm"
              onClick={() => setHistoryOpen(true)}
              title="Show version history"
            >
              <History className="h-3.5 w-3.5" /> History
            </Button>
            {canEdit && plan.status === 'approved' && plan.is_current && (
              openRevision ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => navigate(`/budget/plans/${openRevision.id}`)}
                  title={`A draft revision v${openRevision.version} is already in progress — click to open it`}
                >
                  <GitBranch className="h-3.5 w-3.5" />
                  Open Draft v{openRevision.version}
                </Button>
              ) : (
                <Button size="sm" onClick={() => setReviseOpen(true)} disabled={reviseMut.isPending}>
                  <GitBranch className="h-3.5 w-3.5" />
                  Revise Plan
                </Button>
              )
            )}
            <Button variant="secondary" size="sm" onClick={handleExport}>
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </Button>
            {editable && (
              <>
                <Button
                  variant="secondary" size="sm"
                  onClick={() => importInputRef.current?.click()}
                  disabled={importMut.isPending}
                >
                  <Upload className="h-3.5 w-3.5" />
                  {importMut.isPending ? 'Importing…' : 'Import CSV'}
                </Button>
                <input
                  ref={importInputRef} type="file" accept=".csv,text/csv"
                  className="hidden" onChange={handleImportFile}
                />
              </>
            )}
            {editable && (
              <Button variant="secondary" size="sm" onClick={handleCopyPrev} disabled={copyPrev.isPending}>
                <Copy className="h-3.5 w-3.5" />
                Copy FY {plan.fiscal_year - 1}
              </Button>
            )}
            {editable && (
              <Button size="sm" onClick={() => handleAction('submit')} disabled={planAction.isPending}>
                <Send className="h-3.5 w-3.5" /> Submit
              </Button>
            )}
            {canApprove && (
              <>
                <Button size="sm" onClick={() => handleAction('approve')} disabled={planAction.isPending}>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                </Button>
                <Button size="sm" variant="secondary" onClick={() => handleAction('return')} disabled={planAction.isPending}>
                  <RotateCcw className="h-3.5 w-3.5" /> Return
                </Button>
                <Button size="sm" variant="destructive" onClick={() => handleAction('reject')} disabled={planAction.isPending}>
                  <XCircle className="h-3.5 w-3.5" /> Reject
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Draft-revision-in-progress banner (visible when viewing the approved
          parent of an active draft revision). */}
      {plan.status === 'approved' && plan.is_current && openRevision && (
        <div className="rounded-xl border border-warning-line bg-warning-soft px-4 py-3 flex items-center gap-3">
          <GitBranch className="h-4 w-4 text-warning-fg shrink-0" />
          <p className="flex-1 text-sm text-warning-fg">
            A draft revision <span className="font-mono font-semibold">v{openRevision.version}</span>
            {' '}is already in progress for this plan.
          </p>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => navigate(`/budget/plans/${openRevision.id}`)}
          >
            Open Draft v{openRevision.version}
          </Button>
        </div>
      )}

      {exportError && (
        <ActionMessage onDismiss={() => setExportError(null)}>{exportError}</ActionMessage>
      )}
      {confirmDlg.dialog}

      {/* Import result banner */}
      {importResult && (
        <ImportResultBanner result={importResult} onClose={() => setImportResult(null)} />
      )}

      {/* Revise modal */}
      {reviseOpen && (
        <ReviseModal
          onClose={() => { setReviseOpen(false); setReviseError(null) }}
          onSubmit={async (notes) => {
            try {
              setReviseError(null)
              const next = await reviseMut.mutateAsync({ planId: plan.id, revision_notes: notes || undefined })
              setReviseOpen(false)
              navigate(`/budget/plans/${next.id}`)
            } catch (e: unknown) {
              setReviseError(e instanceof Error ? e.message : 'Revise failed')
            }
          }}
          error={reviseError}
          pending={reviseMut.isPending}
          fiscalYear={plan.fiscal_year}
          version={plan.version}
        />
      )}

      {/* History drawer */}
      {historyOpen && (
        <VersionHistoryDrawer
          costCenterId={plan.cost_center_id}
          fiscalYear={plan.fiscal_year}
          currentPlanId={plan.id}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      {/* Virtualized grid — single scroll container.
          Sticky header (top:0) and sticky totals footer (bottom:0) share the
          same horizontal scroll, so only one scrollbar pair is visible. */}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        <div
          ref={parentRef}
          className="overflow-auto"
          style={{ height: 'calc(100vh - 320px)', minHeight: '400px' }}
        >
          <div style={{ minWidth: 'fit-content', position: 'relative' }}>

            {/* Sticky header */}
            <div
              className="sticky top-0 z-20 grid items-center text-xs font-semibold text-fg-muted uppercase tracking-wide border-b border-line bg-surface-subtle"
              style={{ gridTemplateColumns: GRID_COLS, height: HEADER_HEIGHT }}
            >
              <div className="px-3 text-left sticky left-0 bg-surface-subtle z-10">Account</div>
              {MONTHS.map((m) => (
                <div key={m} className="px-2 text-right">{m}</div>
              ))}
              <div className="px-2 text-right bg-surface-sunken">Q1</div>
              <div className="px-2 text-right bg-surface-sunken">Q2</div>
              <div className="px-2 text-right bg-surface-sunken">Q3</div>
              <div className="px-2 text-right bg-surface-sunken">Q4</div>
              <div className="px-2 text-right bg-primary-50 text-primary-700">Year</div>
            </div>

            {/* Virtualized body */}
            <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
              {rowVirtualizer.getVirtualItems().map((virtualItem) => {
                const row = flatRows[virtualItem.index]
                if (!row) return null
                return (
                  <div
                    key={virtualItem.key}
                    data-index={virtualItem.index}
                    ref={rowVirtualizer.measureElement}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${virtualItem.start}px)`,
                    }}
                  >
                    {row.kind === 'l1' ? (
                      <L1HeaderRow l1_code={row.l1_code} l1_name={row.l1_name} />
                    ) : (
                      <AccountRowMemo
                        planId={plan.id}
                        row={row.row}
                        editable={editable}
                        onOpenBreakdown={(month) => setBreakdownCell({ row: row.row, month })}
                      />
                    )}
                  </div>
                )
              })}
            </div>

            {/* Sticky totals footer */}
            {columnTotals && (
              <TotalsRow totals={columnTotals} />
            )}

          </div>
        </div>
      </div>

      {/* Breakdown matrix modal */}
      {breakdownCell && (
        <BreakdownMatrixModal
          planId={plan.id}
          accountId={breakdownCell.row.account_id}
          accountCode={breakdownCell.row.account_code}
          accountName={breakdownCell.row.account_name}
          month={breakdownCell.month}
          currentTotal={breakdownCell.row.months[breakdownCell.month] ?? 0}
          currentBreakdowns={breakdownCell.row.breakdowns_by_month[breakdownCell.month] ?? []}
          editable={editable}
          onClose={() => setBreakdownCell(null)}
        />
      )}
    </div>
  )
}

// ── L1 group header row ──────────────────────────────────────────────────────

function L1HeaderRow({ l1_name }: { l1_code: string; l1_name: string }) {
  return (
    <div
      className="grid items-center bg-surface-sunken border-b border-line"
      style={{ gridTemplateColumns: GRID_COLS, minWidth: 'fit-content', height: L1_ROW_HEIGHT }}
    >
      <div className="sticky left-0 bg-surface-sunken px-3 z-10 text-sm font-semibold text-fg">
        {l1_name}
      </div>
    </div>
  )
}

// ── Sticky totals footer ─────────────────────────────────────────────────────

function TotalsRow({
  totals,
}: {
  totals: { months: Record<number, number>; q1: number; q2: number; q3: number; q4: number; year: number }
}) {
  return (
    <div
      className="sticky bottom-0 z-20 grid items-center border-t-2 border-line-strong bg-surface-sunken font-semibold text-fg"
      style={{ gridTemplateColumns: GRID_COLS, height: ROW_HEIGHT }}
    >
      <div className="sticky left-0 bg-surface-sunken px-3 z-10 text-sm uppercase tracking-wide text-fg-secondary">
        Total
      </div>
      {Array.from({ length: 12 }, (_, idx) => {
        const m = idx + 1
        return (
          <div key={m} className="px-2 text-right font-mono text-xs">
            {formatAmount(totals.months[m] ?? 0, 'CAD')}
          </div>
        )
      })}
      <div className="px-2 text-right font-mono text-xs bg-surface-strong">{formatAmount(totals.q1, 'CAD')}</div>
      <div className="px-2 text-right font-mono text-xs bg-surface-strong">{formatAmount(totals.q2, 'CAD')}</div>
      <div className="px-2 text-right font-mono text-xs bg-surface-strong">{formatAmount(totals.q3, 'CAD')}</div>
      <div className="px-2 text-right font-mono text-xs bg-surface-strong">{formatAmount(totals.q4, 'CAD')}</div>
      <div className="px-2 text-right font-mono text-xs bg-primary-100 text-primary-800">
        {formatAmount(totals.year, 'CAD')}
      </div>
    </div>
  )
}

// ── Account row ──────────────────────────────────────────────────────────────

interface AccountRowProps {
  planId: string
  row: ApiAccountPlanRow
  editable: boolean
  onOpenBreakdown: (month: number) => void
}

function AccountRow({ planId, row, editable, onOpenBreakdown }: AccountRowProps) {
  return (
    <div
      className="grid items-center border-b border-line-subtle hover:bg-primary-50/30 transition-colors"
      style={{ gridTemplateColumns: GRID_COLS, minWidth: 'fit-content', height: ROW_HEIGHT }}
    >
      <div className="sticky left-0 bg-surface px-3 z-10 border-r border-line-subtle h-full flex items-center gap-2">
        {row.decomposition_enabled && (
          <Layers className="h-3.5 w-3.5 text-primary-600 shrink-0" />
        )}
        <span className="font-mono text-2xs text-fg-muted shrink-0">{row.account_code}</span>
        <span className="text-xs text-fg-secondary truncate">{row.account_name}</span>
      </div>
      {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
        <div key={m} className="text-right h-full flex items-center justify-end">
          {row.decomposition_enabled ? (
            <button
              onClick={() => onOpenBreakdown(m)}
              className="w-full text-right font-mono text-xs text-fg-secondary hover:bg-primary-50 rounded px-1.5 py-1 flex items-center justify-end gap-1"
              title="Edit factor breakdowns"
            >
              <Lock className="h-3 w-3 text-fg-faint shrink-0" />
              {formatAmount(row.months[m] ?? 0, 'CAD')}
            </button>
          ) : (
            <CellInput
              planId={planId}
              accountId={row.account_id}
              month={m}
              value={row.months[m] ?? 0}
              editable={editable}
            />
          )}
        </div>
      ))}
      <QtdCell value={row.q1} />
      <QtdCell value={row.q2} />
      <QtdCell value={row.q3} />
      <QtdCell value={row.q4} />
      <div className="px-2 text-right font-mono text-xs font-semibold bg-primary-50/50 text-primary-700 h-full flex items-center justify-end">
        {formatAmount(row.year_total, 'CAD')}
      </div>
    </div>
  )
}

// Memoize so that typing into a cell on one account doesn't re-render every other account.
const AccountRowMemo = memo(AccountRow, (prev, next) => {
  if (prev.editable !== next.editable) return false
  if (prev.planId !== next.planId) return false
  const a = prev.row, b = next.row
  if (a.account_id !== b.account_id) return false
  if (a.year_total !== b.year_total) return false
  if (a.decomposition_enabled !== b.decomposition_enabled) return false
  for (let m = 1; m <= 12; m++) {
    if ((a.months[m] ?? 0) !== (b.months[m] ?? 0)) return false
  }
  if (a.breakdowns_by_month !== b.breakdowns_by_month) return false
  return true
})

function QtdCell({ value }: { value: number }) {
  return (
    <div className="px-2 text-right font-mono text-xs text-fg-secondary bg-surface-subtle h-full flex items-center justify-end">
      {formatAmount(value, 'CAD')}
    </div>
  )
}

// ── Inline editable cell for non-decomposition months ──────────────────────

function CellInput({
  planId, accountId, month, value, editable,
}: {
  planId: string
  accountId: string
  month: number
  value: number
  editable: boolean
}) {
  const [local, setLocal] = useState<string>(value === 0 ? '' : String(value))
  const updateLine = useUpdatePlanLine()

  const handleBlur = () => {
    const num = parseFloat(local || '0')
    if (Number.isNaN(num)) { setLocal(value === 0 ? '' : String(value)); return }
    if (num === value) return
    updateLine.mutate({ planId, accountId, month, body: { amount: num } })
  }

  if (!editable) {
    return (
      <span className={cn('block text-right font-mono text-xs px-1.5 py-1 w-full', value === 0 ? 'text-fg-faint' : 'text-fg-secondary')}>
        {formatAmount(value, 'CAD')}
      </span>
    )
  }

  return (
    <input
      type="number" step="0.01" min="0"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={handleBlur}
      placeholder="0"
      className="w-full text-right font-mono text-xs px-1.5 py-1 rounded border border-transparent hover:border-line focus:border-primary-600 focus:outline-none focus:bg-surface"
    />
  )
}

// ── Revise modal ──────────────────────────────────────────────────────────────

function ReviseModal({
  onClose, onSubmit, pending, fiscalYear, version, error,
}: {
  onClose: () => void
  onSubmit: (notes: string) => void | Promise<void>
  pending: boolean
  /** A refused revise, shown here (was a browser alert). */
  error?: string | null
  fiscalYear: number
  version: number
}) {
  const [notes, setNotes] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-surface rounded-xl shadow-xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-line">
          <h3 className="text-sm font-semibold">Revise Approved Plan</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-surface-sunken"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex flex-col gap-4 p-5">
          <p className="text-sm text-fg-secondary">
            This will create a new draft <span className="font-mono font-semibold">v{version + 1}</span> for
            FY {fiscalYear}, copying all monthly amounts and factor breakdowns from the current approved version.
            The current version stays active for balance and actual queries until the revision is approved.
          </p>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Reason for revision</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Mid-year reallocation per CFO directive"
              rows={3}
              className="rounded-md border border-line-strong px-3 py-2 text-sm"
            />
            <p className="text-2xs text-fg-muted">
              Stored as <code>revision_notes</code> on the new draft. Visible on the version history.
            </p>
          </div>
          {error && <p role="alert" className="text-sm text-danger-fg">{error}</p>}
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={() => onSubmit(notes.trim())} disabled={pending}>
              <GitBranch className="h-4 w-4" />
              {pending ? 'Creating…' : `Create v${version + 1} Draft`}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Version history drawer ────────────────────────────────────────────────────

function VersionHistoryDrawer({
  costCenterId, fiscalYear, currentPlanId, onClose,
}: {
  costCenterId: string
  fiscalYear: number
  currentPlanId: string
  onClose: () => void
}) {
  const { data: versions = [], isLoading } = usePlanVersions(costCenterId, fiscalYear)

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <aside
        className="w-full max-w-md h-full bg-surface shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="sticky top-0 z-10 flex items-center justify-between bg-surface border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-fg-muted" />
            <h3 className="text-sm font-semibold">Version History — FY {fiscalYear}</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-surface-sunken"><X className="h-4 w-4" /></button>
        </header>
        <div className="flex flex-col gap-2 p-4">
          {isLoading ? (
            <p className="text-sm text-fg-muted">Loading…</p>
          ) : versions.length === 0 ? (
            <p className="text-sm text-fg-muted">No versions found.</p>
          ) : (
            versions.map((v) => {
              const isViewing = v.id === currentPlanId
              return (
                <Link
                  key={v.id}
                  to={`/budget/plans/${v.id}`}
                  onClick={onClose}
                  className={cn(
                    'rounded-lg border px-4 py-3 transition-colors flex items-start justify-between gap-3',
                    isViewing
                      ? 'border-primary-300 bg-primary-50'
                      : 'border-line hover:border-primary-200 hover:bg-surface-subtle',
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-sm font-semibold">v{v.version}</span>
                      <StatusBadge status={v.status as DocumentStatus} />
                      {v.is_current && (
                        <span className="rounded-full bg-primary-100 text-primary-700 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide">
                          Current
                        </span>
                      )}
                      {isViewing && (
                        <span className="rounded-full bg-warning-soft text-warning-fg px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide">
                          Viewing
                        </span>
                      )}
                    </div>
                    {v.revision_notes && (
                      <p className="text-xs text-fg-secondary mt-1">{v.revision_notes}</p>
                    )}
                    <p className="text-2xs text-fg-muted mt-1">
                      {v.approved_at
                        ? `Approved ${formatDate(v.approved_at)}`
                        : v.submitted_at
                          ? `Submitted ${formatDate(v.submitted_at)}`
                          : `Created ${formatDate(v.created_at)}`}
                    </p>
                  </div>
                </Link>
              )
            })
          )}
        </div>
      </aside>
    </div>
  )
}

// ── Import result banner ──────────────────────────────────────────────────────

function ImportResultBanner({
  result, onClose,
}: { result: PlanImportResult; onClose: () => void }) {
  const ok = result.lines_updated > 0 && result.errors.length === 0
  const hasWarnings = result.errors.length > 0
  return (
    <div className={cn(
      'rounded-xl border px-4 py-3.5 flex items-start gap-3',
      ok
        ? 'border-success-line bg-success-soft text-success-fg'
        : hasWarnings
          ? 'border-warning-line bg-warning-soft text-warning-fg'
          : 'border-line bg-surface-subtle text-fg-secondary',
    )}>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">
          {result.lines_updated > 0
            ? `${result.lines_updated} cell${result.lines_updated !== 1 ? 's' : ''} updated across ${result.accounts_touched} account${result.accounts_touched !== 1 ? 's' : ''}.`
            : 'No cells changed.'}
          {result.accounts_skipped > 0 && (
            <> · {result.accounts_skipped} decomposed account{result.accounts_skipped !== 1 ? 's' : ''} skipped.</>
          )}
          {result.breakdowns_cleared > 0 && (
            <> · {result.breakdowns_cleared} factor breakdown{result.breakdowns_cleared !== 1 ? 's' : ''} replaced by imported totals.</>
          )}
        </p>
        {result.errors.length > 0 && (
          <ul className="text-xs mt-1.5 space-y-0.5 list-disc list-inside">
            {result.errors.slice(0, 6).map((e, i) => <li key={i}>{e}</li>)}
            {result.errors.length > 6 && (
              <li>…and {result.errors.length - 6} more issue{result.errors.length - 6 !== 1 ? 's' : ''}.</li>
            )}
          </ul>
        )}
      </div>
      <button onClick={onClose} className="p-1 rounded hover:bg-black/5">
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
