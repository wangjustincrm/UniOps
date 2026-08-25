/**
 * ScheduleCard — 自动同步的间隔设置,Purchase / JV / MDM 三个 tab 共用。
 *
 * 后端三处的语义完全一致(见 epms-api/app/tasks/nc_purchase_sync_scheduler.py):
 *   0 = 关闭自动同步,只剩手动按钮
 *   >0 = 分钟数,上限 1440(一天)
 * 所以这里只做一层薄的分钟/小时换算,真正的钳制在后端。
 *
 * English-only copy per project convention.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Loader2 } from 'lucide-react'
import { epmsApi, financeApi, mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface ScheduleState {
  interval_minutes: number
  next_due_at: string | null
}

export interface ScheduleEndpoint {
  client: 'epms' | 'finance' | 'mdm'
  /** GET 路径,返回体里必须含 interval_minutes 与 next_due_at */
  readPath: string
  /** PATCH 路径,请求体 { minutes } */
  writePath: string
  /** react-query 的 key,与该 tab 的手动区块共用同一份 status 时要保持一致 */
  queryKey: string
}

const CLIENTS = { epms: epmsApi, finance: financeApi, mdm: mdmApi }

const DEFAULT_MAX_MINUTES = 1440

// Same convention as NcPurchaseSyncSection's `localTime` — the Purchase tab renders
// this component right next to that one, both showing the same `next_due_at`, and a
// bare `toLocaleString()` there used to disagree with a properly-converted instant
// right beside it (see the project's UTC-4 off-by-one-day history). One format for
// every instant on this screen.
function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** 分钟数 → 便于阅读的 {amount, unit}。整小时用 Hours,否则用 Minutes。 */
function splitInterval(minutes: number): { amount: string; unit: 'minutes' | 'hours' } {
  if (minutes > 0 && minutes % 60 === 0) {
    return { amount: String(minutes / 60), unit: 'hours' }
  }
  return { amount: String(minutes), unit: 'minutes' }
}

export function ScheduleCard({ endpoint, title, maxMinutes = DEFAULT_MAX_MINUTES }: {
  endpoint: ScheduleEndpoint
  title: string
  maxMinutes?: number
}) {
  const qc = useQueryClient()
  const api = CLIENTS[endpoint.client]
  const queryKey = [endpoint.queryKey]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<ScheduleState>(endpoint.readPath),
  })

  const [enabled, setEnabled] = useState(false)
  const [amount, setAmount] = useState('1')
  const [unit, setUnit] = useState<'minutes' | 'hours'>('hours')
  const [err, setErr] = useState<string | null>(null)

  const serverMinutes = data?.interval_minutes

  // 服务端值到达/变化时同步进本地编辑态。依赖只放服务端那个数,不放本地
  // enabled/amount —— 否则用户每敲一个字都会被服务端值覆盖回去。
  useEffect(() => {
    if (serverMinutes === undefined) return
    setEnabled(serverMinutes > 0)
    if (serverMinutes > 0) {
      const s = splitInterval(serverMinutes)
      setAmount(s.amount)
      setUnit(s.unit)
    }
  }, [serverMinutes])

  // 关闭 = 提交 0。这是后端的语义,不是前端发明的。
  const minutes = enabled ? (unit === 'hours' ? Number(amount) * 60 : Number(amount)) : 0

  function validate(): string | null {
    if (!enabled) return null
    if (!Number.isFinite(minutes) || !Number.isInteger(minutes) || minutes <= 0) {
      return 'Enter a whole number greater than zero.'
    }
    if (minutes > maxMinutes) {
      return `Maximum interval is ${maxMinutes} minutes (24 hours).`
    }
    return null
  }

  const save = useMutation({
    mutationFn: async () => {
      const problem = validate()
      if (problem) throw new Error(problem)
      return api.patch(endpoint.writePath, { minutes })
    },
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey }) },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to save schedule'),
  })

  if (isLoading) {
    return (
      <div className="rounded-xl border border-neutral-200 bg-white px-4 py-6 text-center text-sm text-neutral-400">
        Loading schedule…
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="mb-3 flex items-center gap-2">
        <Clock className="h-4 w-4 text-neutral-500" />
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-neutral-700">
          <input type="checkbox" checked={enabled}
                 onChange={(e) => setEnabled(e.target.checked)} className="h-4 w-4" />
          Run automatically
        </label>

        <span className="text-sm text-neutral-500">every</span>
        <input type="number" min={1} value={amount} disabled={!enabled}
               onChange={(e) => setAmount(e.target.value)}
               className="h-9 w-20 rounded-lg border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400" />
        <select value={unit} disabled={!enabled}
                onChange={(e) => setUnit(e.target.value as 'minutes' | 'hours')}
                className="h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400">
          <option value="minutes">Minutes</option>
          <option value="hours">Hours</option>
        </select>

        <button type="button" disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}
                className={cn('ml-auto flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2',
                              'text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50')}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save
        </button>
      </div>

      <div className="mt-3 border-t border-neutral-100 pt-3 text-xs text-neutral-500">
        Next run: {enabled ? formatWhen(data?.next_due_at ?? null) : 'Disabled'}
      </div>

      <p className="mt-2 text-[11px] text-neutral-400">
        Scheduled runs are always incremental. A full reload must be started manually.
      </p>

      {err && <div className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
    </div>
  )
}
