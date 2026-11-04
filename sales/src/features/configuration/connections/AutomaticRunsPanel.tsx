import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { ActionButton, ConfirmAction, ErrorMessage, Hint, SchemaForm, Skeleton, queryKeys, useCan, type FieldSpec } from '@/platform-ui'
import { connectionsClient, describeSchedule, saveSchedulerSettings, type ConnectionRead } from './connectionsApi'
import { SCHEDULER_KEY, useAutomaticRuns, useShadowSetting } from './connectionSettings'

const minutesSchema = z.object({ system_minutes: z.number({ error: 'Enter minutes' }).int().min(1, 'At least 1').max(1440) })
const minutesFields: FieldSpec[] = [
  { name: 'system_minutes', label: 'System job: every (minutes)', type: 'number', required: true,
    hint: 'Queues 940s and 943s, sends the outbox, times out ACKs, moves invoices on, compares with Zoho in the shadow run.' },
]

/** What turning it on starts, in words. */
function willStart(active: ConnectionRead[], minutes: number, shadow: boolean | undefined): string {
  const running = active.filter((c) => !c.schedule_paused)
  const held = active.filter((c) => c.schedule_paused)
  return [
    running.length ? `These connections start reading now: ${running.map((c) => `${c.name} (${describeSchedule(c)})`).join(', ')}.`
      : 'No active connection has a running schedule yet.',
    held.length ? `Paused on their own, they stay paused: ${held.map((c) => c.name).join(', ')}.` : '',
    `The system job runs every ${minutes} min.`,
    shadow ? 'The shadow run is on: files are read and left in place, and nothing is sent.'
      : 'The shadow run is off: Sales acts on what it reads, and 940s go out for orders that qualify, as each connection\'s mode and the write settings allow.',
  ].filter(Boolean).join(' ')
}

/** Automatic runs (sales.config, audited): the one place to switch them. On asks
 *  first and says what starts; Off does not. On a server that may not run them
 *  there is no switch. Run now works whatever this says. */
export function AutomaticRunsPanel() {
  const qc = useQueryClient()
  const { state, settings, error } = useAutomaticRuns()
  const shadow = useShadowSetting()
  const canEdit = useCan('sales.config')
  const [confirming, setConfirming] = useState(false)
  const active = useQuery({
    queryKey: [...queryKeys.list(connectionsClient), 'active'], enabled: state === 'off',
    queryFn: () => connectionsClient.list({ page: 1, pageSize: 100, filters: [{ field: 'active', op: 'eq', value: 'true' }], sort: [{ field: 'name' }] }),
  })
  const save = async (data: { paused?: boolean; system_minutes?: number }) => {
    await saveSchedulerSettings(data)
    await Promise.all([qc.invalidateQueries({ queryKey: SCHEDULER_KEY }), qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) }),
      qc.invalidateQueries({ queryKey: ['sales', 'health'] })])
  }
  if (error) return <ErrorMessage error={error} />
  if (!state || !settings) return <Skeleton className="h-8 w-full" />
  if (state === 'unavailable') {
    return <p className="text-sm">An administrator must allow automatic runs on this server. Until then a connection is read only with Run now.</p>
  }
  const on = state === 'on'
  return (
    <div className="flex flex-col gap-4 text-sm">
      <section aria-label="Automatic runs" className="flex flex-col gap-2">
        <p className="font-medium">Automatic runs: {on ? 'On' : 'Off'}</p>
        <p>{on ? 'Each connection runs on its own schedule.' : 'No connection is read by itself. Run now still works.'}</p>
        <span>
          <ActionButton size="sm" variant={on ? 'secondary' : 'primary'} permission="sales.config" disabled={!on && !active.data}
            done={on ? 'Automatic runs are off' : undefined}
            onAction={() => (on ? save({ paused: true }) : setConfirming(true))}>
            {on ? 'Turn automatic runs off' : 'Turn automatic runs on'}
          </ActionButton>
        </span>
      </section>
      <ConfirmAction open={confirming} onOpenChange={setConfirming} title="Turn automatic runs on?"
        description={willStart(active.data?.items ?? [], settings.system_minutes, shadow.data?.shadow_mode)}
        confirmLabel="Turn on" onConfirm={() => save({ paused: false })} />
      <div className="flex flex-col gap-2">
        <SchemaForm key={settings.system_minutes} schema={minutesSchema} fields={minutesFields} readOnly={!canEdit}
          defaultValues={{ system_minutes: settings.system_minutes }} changesOnly
          onSubmit={async (v) => { if (v.system_minutes != null) await save({ system_minutes: Number(v.system_minutes) }) }} />
        <Hint as="p">Saving this changes only how often the system job runs.</Hint>
      </div>
    </div>
  )
}
