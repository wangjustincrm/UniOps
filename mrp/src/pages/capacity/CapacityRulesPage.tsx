// Capacity Rules — design §6.6 page 2, Phase 1B Task 5. Master-data list +
// drawer form over mrp-api's /capacity/rules (Task 1's CRUD — see
// capacityApi.ts header). These rules feed resolve_effective_rules(), the
// (later) MPS engine's factory/line capacity ceiling — this page only
// manages the rule records themselves, never runs MPS.
//
// GET is gated server-side by `mrp.report.view`; POST/PATCH/DELETE by
// `mrp.param.write`. The page assumes the caller can at least view (routing
// in Task 7 won't link here otherwise) and gates the write actions (New /
// Edit / Delete) on `mrp.param.write` via usePermissions() — hidden, not
// merely disabled, so a view-only caller never sees a button that would
// 403 (see hooks/usePermissions.ts, mirrors BomExplorerPage's canSync gate).
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pencil, Plus, Trash2, X as XIcon } from 'lucide-react'
import { Button, SortTh, useSortableRows, TBody, THead, Table, Td, Th, Tr, Badge } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { capacityApi, SCOPE_TYPE_LABEL, CONSTRAINT_TYPE_LABEL, type CapacityRule } from './capacityApi'
import { RuleDrawer } from './RuleDrawer'
import { PlanningCalendarSection } from './PlanningCalendarSection'
import { LossRatesSection } from './LossRatesSection'
import { WeekExceptionsSection } from './WeekExceptionsSection'

// design §7's docstring at the schema level (migration `mrp10b`): every
// pre-weekly-rework rule was `is_active=false`d, not deleted, because its
// `limit_value` was a MONTHLY ceiling that would silently read as a WEEKLY
// one (~4x too generous) if left active under the new per-week resolver.
// This banner is the one-time, dismissible surface of that migration event
// — "one-time" meaning "until the planner acknowledges it", tracked in
// localStorage (same pattern ProductionPlanPage.tsx's display-unit toggle
// uses), not a server-side flag: there is no backend field recording
// whether any given user has seen it, and this task adds none.
const BANNER_DISMISSED_KEY = 'mrp.capacityRules.weeklyMigrationBannerDismissed'

function loadBannerDismissed(): boolean {
  try {
    return window.localStorage.getItem(BANNER_DISMISSED_KEY) === '1'
  } catch {
    return false // localStorage unavailable (e.g. privacy mode) — default to showing it
  }
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatQty(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(n)
}

function scopeLabel(rule: CapacityRule): string {
  return rule.scope_ref ? `${SCOPE_TYPE_LABEL[rule.scope_type]} · ${rule.scope_ref}` : SCOPE_TYPE_LABEL[rule.scope_type]
}

export default function CapacityRulesPage() {
  const queryClient = useQueryClient()
  const toasts = useToasts()
  const permsQuery = usePermissions()
  const canWrite = !!permsQuery.data?.permissions['mrp.param.write']

  const rulesQuery = useQuery({
    queryKey: ['capacity-rules'],
    queryFn: () => capacityApi.list(),
  })
  const rules = rulesQuery.data ?? []

  const [drawerRule, setDrawerRule] = useState<CapacityRule | 'new' | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<CapacityRule | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(() => loadBannerDismissed())

  function dismissBanner() {
    setBannerDismissed(true)
    try {
      window.localStorage.setItem(BANNER_DISMISSED_KEY, '1')
    } catch {
      // localStorage unavailable — the banner just reappears next visit, not a functional problem
    }
  }

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ['capacity-rules'] })
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await capacityApi.remove(deleteTarget.id)
      toasts.success(`Deleted rule — ${scopeLabel(deleteTarget)} · ${CONSTRAINT_TYPE_LABEL[deleteTarget.constraint_type]}.`)
      setDeleteTarget(null)
      await invalidate()
    } catch (err) {
      toasts.error(errMsg(err, 'Could not delete this rule — please retry.'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-fg">Capacity Rules</h1>
        {canWrite && (
          <Button type="button" size="sm" onClick={() => setDrawerRule('new')}>
            <Plus className="h-3.5 w-3.5" /> New rule
          </Button>
        )}
      </div>

      {/* One-time migration banner — design §5.4/§7: every rule that existed
          before the weekly rework was deactivated (mrp10b), not deleted,
          because a monthly ceiling silently misread as a weekly one is a
          ~4x capacity error with no other signal anywhere in the UI. */}
      {!bannerDismissed && (
        <div role="status" className="flex items-start justify-between gap-3 rounded-lg border border-warning-line bg-warning-soft px-4 py-3 text-sm text-warning-fg">
          <span>
            Capacity rules now read per week. Existing rules were deactivated by the weekly migration —
            please re-enter them.
          </span>
          <Button variant="subtle" size="icon-xs"
            type="button"
            onClick={dismissBanner}
            aria-label="Dismiss this notice"
            className="min-h-[44px] min-w-[44px] shrink-0"
          >
            <XIcon className="h-4 w-4" />
          </Button>
        </div>
      )}

      <PlanningCalendarSection canWrite={canWrite} />

      <LossRatesSection canWrite={canWrite} />
      <WeekExceptionsSection canWrite={canWrite} notifySuccess={toasts.success} notifyError={toasts.error} />

      {rulesQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          {errMsg(rulesQuery.error, 'Could not load capacity rules.')}
        </p>
      )}

      {rulesQuery.isLoading ? (
        <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-fg-muted">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading capacity rules…
        </p>
      ) : (
        <RulesTable rules={rules} canWrite={canWrite} onEdit={setDrawerRule} onDelete={setDeleteTarget} />
      )}

      {drawerRule && (
        <RuleDrawer
          rule={drawerRule === 'new' ? null : drawerRule}
          existingRules={rules}
          onClose={() => setDrawerRule(null)}
          onSaved={() => { void invalidate() }}
          notifySuccess={toasts.success}
          notifyError={toasts.error}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete capacity rule?"
          confirmLabel="Delete"
          danger
          busy={deleting}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
        >
          <p>
            This removes the {CONSTRAINT_TYPE_LABEL[deleteTarget.constraint_type]} rule for{' '}
            <strong>{scopeLabel(deleteTarget)}</strong> ({formatQty(Number(deleteTarget.limit_value))}
            {deleteTarget.uom ? ` ${deleteTarget.uom}` : ''}). This cannot be undone.
          </p>
        </ConfirmDialog>
      )}

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}

const RULE_TH = 'border-b border-line px-3 py-2 text-left text-2xs font-semibold text-fg-secondary'

function RulesTable({
  rules, canWrite, onEdit, onDelete,
}: {
  rules: CapacityRule[]
  canWrite: boolean
  onEdit: (rule: CapacityRule) => void
  onDelete: (rule: CapacityRule) => void
}) {
  // Whole list loaded: client sort, each column by what it shows.
  const { rows: sortedRules, sort, onSort } = useSortableRows(rules, {
    scope: (r) => scopeLabel(r),
    constraint: (r) => CONSTRAINT_TYPE_LABEL[r.constraint_type],
    limit: (r) => Number(r.limit_value),
    uom: (r) => r.uom,
    effective: (r) => r.effective_from,
    status: (r) => (r.is_active ? 'Active' : 'Inactive'),
  })
  if (rules.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line-strong py-16 text-center">
        <p className="text-sm text-fg-muted">No capacity rules yet.</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <Table density="compact" className="min-w-full">
        <THead>
          <Tr>
            <SortTh label="Scope" sortKey="scope" sort={sort} onSort={onSort} className={RULE_TH} />
            <SortTh label="Constraint" sortKey="constraint" sort={sort} onSort={onSort} className={RULE_TH} />
            <SortTh label="Limit" sortKey="limit" sort={sort} onSort={onSort} align="right" className="border-b border-line px-3 py-2 text-2xs font-semibold text-fg-secondary" />
            <SortTh label="Unit" sortKey="uom" sort={sort} onSort={onSort} className={RULE_TH} />
            <SortTh label="Effective" sortKey="effective" sort={sort} onSort={onSort} className={RULE_TH} />
            <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={RULE_TH} />
            {canWrite && <Th align="right" className="border-b border-line ">Actions</Th>}
          </Tr>
        </THead>
        <TBody>
          {sortedRules.map((rule) => (
            <Tr key={rule.id} className="odd:bg-surface even:bg-surface-subtle/50">
              <Td className="text-fg">{scopeLabel(rule)}</Td>
              <Td className="text-fg-secondary">{CONSTRAINT_TYPE_LABEL[rule.constraint_type]}</Td>
              <Td align="right" className="font-mono text-xs text-fg">{formatQty(Number(rule.limit_value))}</Td>
              <Td className="text-xs text-fg-secondary">{rule.uom ?? '—'}</Td>
              <Td className="text-xs text-fg-secondary">
                {formatDate(rule.effective_from)} – {rule.effective_to ? formatDate(rule.effective_to) : 'open-ended'}
              </Td>
              <Td>
                <Badge variant={rule.is_active ? 'success' : 'neutral'}>
                  {rule.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </Td>
              {canWrite && (
                <Td align="right">
                  {/* Design spec §6.6 mandates >=44x44px interactive targets.
                      The buttons are h-11 w-11 (44px) so the clickable area
                      itself meets that, even though the glyph inside stays
                      small (h-3.5 w-3.5) to match the table's density. */}
                  <div className="flex justify-end">
                    <Button variant="subtle" size="icon-xs"
                      type="button"
                      onClick={() => onEdit(rule)}
                      aria-label={`Edit ${scopeLabel(rule)} rule`}
                     
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="danger-ghost" size="icon-xs"
                      type="button"
                      onClick={() => onDelete(rule)}
                      aria-label={`Delete ${scopeLabel(rule)} rule`}
                     
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </Td>
              )}
            </Tr>
          ))}
        </TBody>
      </Table>
    </div>
  )
}
