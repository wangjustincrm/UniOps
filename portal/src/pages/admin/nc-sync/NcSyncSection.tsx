/**
 * NC Sync — 把 NC65 的三类同步(采购单 / 凭证 / 主数据)合并到一个 Admin 菜单下,
 * 每类各自一张间隔配置卡 + 各自的手动触发区块。
 *
 * 三个 tab 打三个不同的服务:
 *   Purchase → epms-api    JV → finance-api    MDM → mdm-api
 * Portal 与 Finance 是两个独立的 Vite 应用、没有共享 UI 包,所以 JV 的手动触发
 * 区块在这里另写一份(Finance 页面上那个弹窗保持不动,打的是同一个端点)。
 *
 * English-only copy per project convention.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseZap, Loader2 } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { NcPurchaseSyncSection } from '../NcPurchaseSyncSection'
import { ScheduleCard, type ScheduleEndpoint } from './ScheduleCard'

import { Button, Input, Radio, Tabs } from '@uniops/shell'
const FULL_CONFIRM = 'FULL RELOAD'

type Tab = 'purchase' | 'jv' | 'mdm'

const TABS: { key: Tab; label: string }[] = [
  { key: 'purchase', label: 'Purchase Sync' },
  { key: 'jv', label: 'JV Sync' },
  { key: 'mdm', label: 'MDM Sync' },
]

// 三个服务的排期端点。epms 与 finance 把排期放在各自的 /status 里,
// mdm 单开了 /erp/sync/schedule(它的 /sync/status 形状是按 kind 分组的,不能动)。
const ENDPOINTS: Record<Tab, ScheduleEndpoint> = {
  purchase: {
    client: 'epms',
    readPath: '/admin/nc-purchase-sync/status',
    writePath: '/admin/nc-purchase-sync/interval',
    queryKey: 'nc-purchase-sync-status',
  },
  jv: {
    client: 'finance',
    readPath: '/nc-sync/status',
    writePath: '/nc-sync/interval',
    queryKey: 'nc-sync-status',
  },
  mdm: {
    client: 'mdm',
    readPath: '/erp/sync/schedule',
    writePath: '/erp/sync/interval',
    queryKey: 'erp-sync-schedule',
  },
}

interface NcSyncRun {
  id: string; mode: string; status: string
  started_at: string | null; watermark_to: string | null
  vouchers_inserted: number; vouchers_deleted: number
  unmapped_cc_count: number; error: string | null
}
interface NcSyncStatus {
  can_sync: boolean; configured: boolean
  current_run: NcSyncRun | null; last_run: NcSyncRun | null
}

function JvRunSummary({ run, label }: { run: NcSyncRun; label: string }) {
  return (
    <div className="rounded-lg bg-surface-subtle px-3 py-2 text-xs text-fg-secondary">
      <span className="font-medium text-fg-secondary">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${formatDateTime(run.started_at)}`}
      {run.status !== 'running' && (
        <> · inserted {Number(run.vouchers_inserted).toLocaleString()} vouchers
          {run.vouchers_deleted > 0 && `, deleted ${Number(run.vouchers_deleted).toLocaleString()}`}
          {run.unmapped_cc_count > 0 && `, ${Number(run.unmapped_cc_count).toLocaleString()} unmapped CC lines`}
        </>
      )}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.error && <div className="mt-1 text-danger-fg">{run.error}</div>}
    </div>
  )
}

function JvManualSync() {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  // 与 ScheduleCard 共用同一个 queryKey —— 排期字段和 run 状态来自同一个端点,
  // 保存间隔后这里的展示也会跟着刷新。
  const { data: status } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null

  const start = async () => {
    if (starting) return
    setErr(null); setStarting(true)
    try {
      await financeApi.post('/nc-sync', {
        mode, confirm: mode === 'full' ? confirm : undefined,
      })
      setConfirm('')
      await qc.invalidateQueries({ queryKey: ['nc-sync-status'] })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to start sync')
    } finally {
      setStarting(false)
    }
  }

  const canStart = !!status?.configured && !!status?.can_sync && !running && !starting &&
    (mode === 'incremental' || confirm === FULL_CONFIRM)

  if (!status) return <div className="py-6 text-center text-sm text-fg-muted">Loading…</div>
  if (!status.configured) {
    return (
      <div className="rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-sm text-warning-fg">
        NC voucher sync is not configured. Contact an administrator to set up the NC65 connection.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {status.last_run && !running && <JvRunSummary run={status.last_run} label="Last sync" />}
      {running ? (
        <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-3 text-sm text-fg-secondary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Sync in progress ({running.mode})…
        </div>
      ) : (
        <>
          <div className="space-y-2 text-sm">
            <label className="flex items-start gap-2">
              <Radio checked={mode === 'incremental'}
                     onChange={() => setMode('incremental')} className="mt-0.5" />
              <span>
                <span className="font-medium">Incremental</span>
                <span className="block text-xs text-fg-muted">
                  Import only vouchers that are new or changed in NC since the last watermark.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <Radio checked={mode === 'full'}
                     onChange={() => setMode('full')} className="mt-0.5" />
              <span>
                <span className="font-medium">Full reload</span>
                <span className="block text-xs text-fg-muted">Re-import every NC voucher.</span>
              </span>
            </label>
          </div>
          {mode === 'full' && (
            <div className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-danger-fg">
                <AlertTriangle className="h-3.5 w-3.5" />
                Destructive: re-imports every NC voucher. Type {FULL_CONFIRM} to enable.
              </div>
              <Input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                     placeholder={FULL_CONFIRM} className="font-mono" />
            </div>
          )}
          {err && <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{err}</div>}
          <div className="flex justify-end border-t border-line-subtle pt-3">
            <Button size="md" onClick={start} disabled={!canStart}>
              {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
              Start Sync
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

export function NcSyncSection() {
  const [tab, setTab] = useState<Tab>('purchase')

  return (
    <div>
      <div className="mb-6 border-b border-line-subtle pb-4">
        <h2 className="text-lg font-semibold text-fg">NC Sync</h2>
        <p className="mt-0.5 text-sm text-fg-muted">
          Import purchase orders, journal vouchers and master data from NC65 — on a schedule or on demand.
        </p>
      </div>

      <Tabs className="mb-4" items={TABS.map((t) => ({ key: t.key, label: t.label }))} value={tab} onChange={setTab} />

      {tab === 'purchase' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.purchase} title="Automatic purchase sync" />
          <NcPurchaseSyncSection />
        </div>
      )}

      {tab === 'jv' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.jv} title="Automatic voucher sync" />
          <JvManualSync />
        </div>
      )}

      {tab === 'mdm' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.mdm} title="Automatic master-data sync" />
          <p className="text-sm text-fg-muted">
            A scheduled run imports materials, suppliers and persons in that order.
            Browse and manually sync the mirrored records under ERP MDM.
          </p>
        </div>
      )}
    </div>
  )
}
