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
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { capacityApi, SCOPE_TYPE_LABEL, CONSTRAINT_TYPE_LABEL, type CapacityRule } from './capacityApi'
import { RuleDrawer } from './RuleDrawer'
import { PlanningCalendarSection } from './PlanningCalendarSection'
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
        <h1 className="text-lg font-semibold text-neutral-900">Capacity Rules</h1>
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
        <div role="status" className="flex items-start justify-between gap-3 rounded-lg border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800">
          <span>
            Capacity rules now read per week. Existing rules were deactivated by the weekly migration —
            please re-enter them.
          </span>
          <button
            type="button"
            onClick={dismissBanner}
            aria-label="Dismiss this notice"
            className="flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center text-warning-600 hover:text-warning-900"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>
      )}

      <PlanningCalendarSection canWrite={canWrite} />
      <WeekExceptionsSection canWrite={canWrite} />

      {rulesQuery.isError && (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {errMsg(rulesQuery.error, 'Could not load capacity rules.')}
        </p>
      )}

      {rulesQuery.isLoading ? (
        <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-neutral-400">
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

function RulesTable({
  rules, canWrite, onEdit, onDelete,
}: {
  rules: CapacityRule[]
  canWrite: boolean
  onEdit: (rule: CapacityRule) => void
  onDelete: (rule: CapacityRule) => void
}) {
  if (rules.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-16 text-center">
        <p className="text-sm text-neutral-500">No capacity rules yet.</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200">
      <table className="min-w-full text-sm">
        <thead className="bg-neutral-50">
          <tr>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Scope</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Constraint</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Limit</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Unit</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Effective</th>
            <th className="border-b border-neutral-200 px-3 py-2 text-left text-[11px] font-semibold text-neutral-600">Status</th>
            {canWrite && <th className="border-b border-neutral-200 px-3 py-2 text-right text-[11px] font-semibold text-neutral-600">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.id} className="border-b border-neutral-100 last:border-0 odd:bg-white even:bg-neutral-50/50">
              <td className="px-3 py-2 text-neutral-800">{scopeLabel(rule)}</td>
              <td className="px-3 py-2 text-neutral-600">{CONSTRAINT_TYPE_LABEL[rule.constraint_type]}</td>
              <td className="px-3 py-2 text-right font-mono text-xs text-neutral-800">{formatQty(Number(rule.limit_value))}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">{rule.uom ?? '—'}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">
                {formatDate(rule.effective_from)} – {rule.effective_to ? formatDate(rule.effective_to) : 'open-ended'}
              </td>
              <td className="px-3 py-2">
                <span
                  className={
                    rule.is_active
                      ? 'inline-flex items-center rounded-full bg-success-50 px-2 py-0.5 text-xs font-medium text-success-700 ring-1 ring-inset ring-emerald-200'
                      : 'inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-500 ring-1 ring-inset ring-neutral-200'
                  }
                >
                  {rule.is_active ? 'Active' : 'Inactive'}
                </span>
              </td>
              {canWrite && (
                <td className="px-1 py-1 text-right">
                  {/* Design spec §6.6 mandates >=44x44px interactive targets.
                      The buttons are h-11 w-11 (44px) so the clickable area
                      itself meets that, even though the glyph inside stays
                      small (h-3.5 w-3.5) to match the table's density. */}
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => onEdit(rule)}
                      aria-label={`Edit ${scopeLabel(rule)} rule`}
                      className="flex h-11 w-11 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(rule)}
                      aria-label={`Delete ${scopeLabel(rule)} rule`}
                      className="flex h-11 w-11 items-center justify-center rounded-md text-neutral-400 hover:bg-danger-50 hover:text-danger-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
