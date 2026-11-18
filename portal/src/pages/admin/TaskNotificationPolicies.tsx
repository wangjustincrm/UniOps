import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, CheckCircle2, Loader2 } from 'lucide-react'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'

import { Checkbox, Select, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
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
    <div className="mt-8 border-t border-line pt-6">
      <h3 className="text-base font-semibold text-fg">Task Notifications</h3>
      <p className="mt-1 max-w-3xl text-sm text-fg-muted">
        Choose which tasks email the person they are assigned to, and when.
        <span className="font-medium text-fg-secondary"> Immediately</span> sends one email as soon as the task is created;
        <span className="font-medium text-fg-secondary"> Daily digest</span> sends each person one email a day, at the
        digest time above, listing all of their open tasks of those types. Applies to tasks created from now on.
        People who turned notifications off in their own profile are not emailed.
      </p>

      {isLoading && <div className="py-8 text-center text-sm text-fg-muted">Loading…</div>}
      {isError && (
        <div className="mt-4 flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertCircle className="h-4 w-4" /> Couldn't load task types: {(error as Error)?.message}
        </div>
      )}

      {groups.map(([module, rows]) => (
        <div key={module} className="mt-5">
          <h4 className="mb-2 text-xs font-semibold text-fg-muted">{module}</h4>
          <div className="overflow-hidden rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr>
                  <Th>Task</Th>
                  <Th className="w-20">Email</Th>
                  <Th className="w-48">When</Th>
                </Tr>
              </THead>
              <TBody>
                {rows.map((p) => {
                  const c = current(p)
                  const changed = !!draft[p.task_type]
                  return (
                    <Tr key={p.task_type} className={cn('border-t border-line-subtle', changed && 'bg-warning-soft/60')}>
                      <Td>
                        <div className="text-fg">{p.label}</div>
                        <div className="font-mono text-2xs text-fg-muted">{p.task_type}</div>
                      </Td>
                      {p.note ? (
                        <Td colSpan={2} className="text-xs text-fg-muted">{p.note}</Td>
                      ) : (
                        <>
                          <Td>
                            <Checkbox
                             
                              aria-label={`Email for ${p.label}`}
                              checked={c.email}
                              onChange={(e) => set(p, { email: e.target.checked })}
                              className="accent-[#085E5E]"
                            />
                          </Td>
                          <Td>
                            <Select selectSize="sm"
                              aria-label={`When to email for ${p.label}`}
                              value={c.mode}
                              disabled={!c.email}
                              onChange={(e) => set(p, { mode: e.target.value as Mode })}
                              className="w-full"
                            >
                              <option value="immediate">Immediately</option>
                              <option value="digest">Daily digest</option>
                            </Select>
                          </Td>
                        </>
                      )}
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          </div>
        </div>
      ))}

      {policies.length > 0 && (
        <div className="mt-5 flex items-center gap-3">
          <Button size="md"
            type="button"
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
           
          >
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {save.isPending ? 'Saving…' : 'Save Task Notifications'}
          </Button>
          {dirty && !save.isPending && (
            <Button variant="link" type="button" onClick={() => setDraft({})} className="text-sm text-fg-muted">
              Discard changes
            </Button>
          )}
          {toast && (
            <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-sm', toast.ok ? 'bg-success-soft text-success-fg' : 'bg-danger-soft text-danger-fg')}>
              {toast.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {toast.msg}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
