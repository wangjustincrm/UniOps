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
import { cn } from '@/lib/utils'
import { NcPurchaseSyncSection } from '../NcPurchaseSyncSection'
import { ScheduleCard, type ScheduleEndpoint } from './ScheduleCard'

const FULL_CONFIRM = 'FULL RELOAD'
const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'

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
    <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
      <span className="font-medium text-neutral-700">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${run.started_at.slice(0, 16).replace('T', ' ')}`}
      {run.status !== 'running' && (
        <> · inserted {Number(run.vouchers_inserted).toLocaleString()} vouchers
          {run.vouchers_deleted > 0 && `, deleted ${Number(run.vouchers_deleted).toLocaleString()}`}
          {run.unmapped_cc_count > 0 && `, ${Number(run.unmapped_cc_count).toLocaleString()} unmapped CC lines`}
        </>
      )}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.error && <div className="mt-1 text-red-600">{run.error}</div>}
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

  if (!status) return <div className="py-6 text-center text-sm text-neutral-400">Loading…</div>
  if (!status.configured) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        NC voucher sync is not configured. Contact an administrator to set up the NC65 connection.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {status.last_run && !running && <JvRunSummary run={status.last_run} label="Last sync" />}
      {running ? (
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 px-3 py-3 text-sm text-neutral-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          Sync in progress ({running.mode})…
        </div>
      ) : (
        <>
          <div className="space-y-2 text-sm">
            <label className="flex items-start gap-2">
              <input type="radio" checked={mode === 'incremental'}
                     onChange={() => setMode('incremental')} className="mt-0.5" />
              <span>
                <span className="font-medium">Incremental</span>
                <span className="block text-xs text-neutral-500">
                  Import only vouchers that are new or changed in NC since the last watermark.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="radio" checked={mode === 'full'}
                     onChange={() => setMode('full')} className="mt-0.5" />
              <span>
                <span className="font-medium">Full reload</span>
                <span className="block text-xs text-neutral-500">Re-import every NC voucher.</span>
              </span>
            </label>
          </div>
          {mode === 'full' && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-red-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                Destructive: re-imports every NC voucher. Type {FULL_CONFIRM} to enable.
              </div>
              <input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                     placeholder={FULL_CONFIRM} className={cn(inputCls, 'w-full font-mono')} />
            </div>
          )}
          {err && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
          <div className="flex justify-end border-t border-neutral-100 pt-3">
            <button onClick={start} disabled={!canStart} className={primaryBtn}>
              {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
              Start Sync
            </button>
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
      <div className="mb-6 border-b border-neutral-100 pb-4">
        <h2 className="text-lg font-semibold text-neutral-900">NC Sync</h2>
        <p className="mt-0.5 text-sm text-neutral-500">
          Import purchase orders, journal vouchers and master data from NC65 — on a schedule or on demand.
        </p>
      </div>

      <div className="mb-4 flex gap-1 border-b border-neutral-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={cn('-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              tab === t.key
                ? 'border-[#085E5E] text-[#085E5E]'
                : 'border-transparent text-neutral-500 hover:text-neutral-700')}>
            {t.label}
          </button>
        ))}
      </div>

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
          <p className="text-sm text-neutral-500">
            A scheduled run imports materials, suppliers and persons in that order.
            Browse and manually sync the mirrored records under ERP MDM.
          </p>
        </div>
      )}
    </div>
  )
}
