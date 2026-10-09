import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, CheckCircle2, Loader2 } from 'lucide-react'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'

// Per-task-type email policy — epms-api GET/PUT /config/task-notifications.
// The list of task types comes from the server (derived from the task
// registries, OA claim types and the tasks table), so a new task type shows up
// here on its own; nothing on this page names a type.

type Mode = 'immediate' | 'digest'

interface Policy {
  task_type: string
  label: string
  module: string
  email: boolean
  mode: Mode
  default_email: boolean
  note: string | null
}

type Draft = Record<string, { email: boolean; mode: Mode }>

export function TaskNotificationPolicies() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['task-notifications'],
    queryFn: () => epmsApi.get<{ policies: Policy[] }>('/config/task-notifications'),
  })
  const [draft, setDraft] = useState<Draft>({})
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const policies = data?.policies ?? []
  const current = (p: Policy) => draft[p.task_type] ?? { email: p.email, mode: p.mode }
  const dirty = Object.keys(draft).length > 0

  const groups = useMemo(() => {
    const out: Record<string, Policy[]> = {}
    for (const p of policies) (out[p.module] ??= []).push(p)
    return Object.entries(out)
  }, [policies])

  const save = useMutation({
    mutationFn: () => epmsApi.put<{ policies: Policy[] }>('/config/task-notifications', {
      policies: policies.filter((p) => !p.note).map((p) => ({ task_type: p.task_type, ...current(p) })),
    }),
    onSuccess: (res) => {
      qc.setQueryData(['task-notifications'], res)
      setDraft({})
      setToast({ ok: true, msg: 'Task notification settings saved.' })
    },
    onError: (err: Error) => setToast({ ok: false, msg: err.message }),
  })

  const set = (p: Policy, patch: Partial<{ email: boolean; mode: Mode }>) => {
    setToast(null)
    const next = { ...current(p), ...patch }
    setDraft((d) => {
      const copy = { ...d }
      if (next.email === p.email && next.mode === p.mode) delete copy[p.task_type]
      else copy[p.task_type] = next
      return copy
    })
  }

  return (
    <div className="mt-8 border-t border-neutral-200 pt-6">
      <h3 className="text-base font-semibold text-neutral-900">Task Notifications</h3>
      <p className="mt-1 max-w-3xl text-sm text-neutral-500">
        Choose which tasks email the person they are assigned to, and when.
        <span className="font-medium text-neutral-700"> Immediately</span> sends one email as soon as the task is created;
        <span className="font-medium text-neutral-700"> Daily digest</span> sends each person one email a day, at the
        digest time above, listing all of their open tasks of those types. Applies to tasks created from now on.
        People who turned notifications off in their own profile are not emailed.
      </p>

      {isLoading && <div className="py-8 text-center text-sm text-neutral-400">Loading…</div>}
      {isError && (
        <div className="mt-4 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-4 w-4" /> Couldn't load task types: {(error as Error)?.message}
        </div>
      )}

      {groups.map(([module, rows]) => (
        <div key={module} className="mt-5">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">{module}</h4>
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Task</th>
                  <th className="w-20 px-3 py-2 font-medium">Email</th>
                  <th className="w-48 px-3 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => {
                  const c = current(p)
                  const changed = !!draft[p.task_type]
                  return (
                    <tr key={p.task_type} className={cn('border-t border-neutral-100', changed && 'bg-amber-50/60')}>
                      <td className="px-3 py-2">
                        <div className="text-neutral-800">{p.label}</div>
                        <div className="font-mono text-[11px] text-neutral-400">{p.task_type}</div>
                      </td>
                      {p.note ? (
                        <td colSpan={2} className="px-3 py-2 text-xs text-neutral-500">{p.note}</td>
                      ) : (
                        <>
                          <td className="px-3 py-2">
                            <input
                              type="checkbox"
                              aria-label={`Email for ${p.label}`}
                              checked={c.email}
                              onChange={(e) => set(p, { email: e.target.checked })}
                              className="h-4 w-4 accent-[#085E5E]"
                            />
                          </td>
                          <td className="px-3 py-2">
                            <select
                              aria-label={`When to email for ${p.label}`}
                              value={c.mode}
                              disabled={!c.email}
                              onChange={(e) => set(p, { mode: e.target.value as Mode })}
                              className="w-full rounded-md border border-neutral-200 bg-white px-2 py-1 text-sm disabled:bg-neutral-50 disabled:text-neutral-400"
                            >
                              <option value="immediate">Immediately</option>
                              <option value="digest">Daily digest</option>
                            </select>
                          </td>
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {policies.length > 0 && (
        <div className="mt-5 flex items-center gap-3">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60 transition-colors"
          >
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {save.isPending ? 'Saving…' : 'Save Task Notifications'}
          </button>
          {dirty && !save.isPending && (
            <button type="button" onClick={() => setDraft({})} className="text-sm text-neutral-500 hover:underline">
              Discard changes
            </button>
          )}
          {toast && (
            <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-sm', toast.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
              {toast.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {toast.msg}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
