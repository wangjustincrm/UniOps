/**
 * Behaviour parameters.
 *
 * What the module chases and when. The statutory limits themselves are not
 * here and cannot be — they are set in law.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'

interface Config {
  capa_remind_before_days: number
  capa_escalate_supervisor_days: number
  capa_escalate_manager_days: number
  cert_warn_days: number[]
  allow_anonymous_report: boolean
  statutory_scan_interval_minutes: number
}

export default function RulesPage() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['settings', 'config'],
    queryFn: () => api.get<Config>('/api/v1/settings/config'),
  })
  const [form, setForm] = useState<Config | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useMutation({
    mutationFn: (body: Partial<Config>) => api.put('/api/v1/settings/config', body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings', 'config'] }),
  })

  if (!form) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  const num = 'min-h-[44px] w-24 rounded-lg border border-neutral-300 px-2.5 text-sm tabular-nums'

  return (
    <div className="p-4">
      <h1 className="text-lg font-bold tracking-tight text-neutral-900">Rules</h1>
      <p className="mb-4 text-xs text-neutral-500">
        When the module chases things. The 48-hour, three-business-day and 21-day
        limits are set in law and are not editable — only the holiday list the
        business-day count depends on is maintained, and that is done centrally.
      </p>

      <section className="mb-4 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-neutral-900">Corrective actions</h2>
        <p className="mb-3 text-xs text-neutral-500">
          A reminder goes to the owner first. Later steps widen the audience.
        </p>
        <Row label="Remind the owner this many days before it is due">
          <input type="number" min={0} max={90} className={num}
                 value={form.capa_remind_before_days}
                 onChange={(e) => setForm({ ...form, capa_remind_before_days: +e.target.value })} />
        </Row>
        <Row label="Bring in the supervisor after this many days overdue">
          <input type="number" min={1} max={180} className={num}
                 value={form.capa_escalate_supervisor_days}
                 onChange={(e) => setForm({ ...form, capa_escalate_supervisor_days: +e.target.value })} />
        </Row>
        <Row label="Escalate to the HSE Manager after this many days overdue">
          <input type="number" min={1} max={365} className={num}
                 value={form.capa_escalate_manager_days}
                 onChange={(e) => setForm({ ...form, capa_escalate_manager_days: +e.target.value })} />
        </Row>
      </section>

      <section className="mb-4 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-neutral-900">Reporting</h2>
        <Row label="Allow anonymous incident reports">
          <input type="checkbox" className="h-4 w-4" checked={form.allow_anonymous_report}
                 onChange={(e) => setForm({ ...form, allow_anonymous_report: e.target.checked })} />
        </Row>
        <Row label="Check deadlines every this many minutes (0 turns the sweep off)">
          <input type="number" min={0} max={1440} className={num}
                 value={form.statutory_scan_interval_minutes}
                 onChange={(e) => setForm({ ...form, statutory_scan_interval_minutes: +e.target.value })} />
        </Row>
      </section>

      {save.isError && (
        <p className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {(save.error as ApiError).message}
        </p>
      )}
      {save.isSuccess && !save.isPending && (
        <p className="mb-2 rounded-lg bg-success-50 px-3 py-2 text-sm text-success-700">Saved.</p>
      )}
      <button
        type="button"
        disabled={save.isPending}
        onClick={() => { if (!save.isPending) save.mutate(form) }}
        className="min-h-[44px] w-full rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50 sm:w-auto sm:px-6"
      >
        {save.isPending ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-h-[44px] flex-wrap items-center justify-between gap-3 border-b border-neutral-100 py-2 last:border-b-0">
      <span className="min-w-0 flex-1 text-sm text-neutral-700">{label}</span>
      {children}
    </label>
  )
}
