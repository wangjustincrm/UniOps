import { useState } from 'react'
import { z } from 'zod'
import {
  ActionButton, Badge, ConfirmAction, FormDialog, columnsOf, LocalTable, NoticeScope, RowActions, SchemaForm, Section, Text,
  type ColumnDef, type FieldSpec, Icon,
} from '@/platform-ui'
import { RotateCcw } from 'lucide-react'
import { clearLogLevel, raiseLogLevel, type HealthRead, type LogOverride } from './healthApi'

const CONFIG = 'sales.config'
const col = columnsOf<LogOverride>()
const HEALTH = [['sales', 'health']]
const name = (logger: string) => logger || 'Everything (root)'

/** Health → Log levels (user 2026-11-23, ≈ Serilog's level override): raise a logger for a while to see more while
 *  chasing an error; it goes back by itself (at most 24 hours). Output goes to the server's console log only. */
export function LogLevelsSection({ health }: { health: HealthRead }) {
  return <NoticeScope><LogLevels health={health} /></NoticeScope>
}

function LogLevels({ health }: { health: HealthRead }) {
  const v = health.log_levels
  const [raising, setRaising] = useState(false)
  const [clearing, setClearing] = useState<LogOverride | null>(null)
  if (!v) return null
  const schema = z.object({
    logger: z.string().max(100), level: z.string().min(1, 'Required'),
    minutes: z.number({ message: 'Required' }).int().min(1, 'At least 1 minute').max(v.max_minutes, `At most ${v.max_minutes / 60} hours`),
    reason: z.string().trim().min(3, 'Say why (a few words)'),
  })
  const fields: FieldSpec[] = [
    { name: 'logger', label: 'Area', type: 'select', options: Object.entries(v.suggested).map(([value, label]) => ({ value, label: `${label}${value ? ` (${value})` : ''}` })) },
    { name: 'level', label: 'Level', type: 'select', required: true, options: v.levels.map((l) => ({ value: l, label: l })) },
    { name: 'minutes', label: 'For (minutes)', type: 'number', required: true, hint: `It goes back by itself after this; at most ${v.max_minutes / 60} hours.` },
    { name: 'reason', label: 'Why', type: 'textarea', required: true },
  ]
  const columns: ColumnDef<LogOverride, unknown>[] = [
    { accessorKey: 'logger', header: 'Area', cell: ({ row: { original: o } }) => name(o.logger) },
    { accessorKey: 'level', header: 'Level', cell: ({ row: { original: o } }) => <Badge variant="warning">{o.level}</Badge> },
    col.datetime('until', 'Until'),
    { accessorKey: 'by', header: 'By', cell: ({ row: { original: o } }) => <Text size="xs">{o.by ?? ''}{o.reason ? ` — ${o.reason}` : ''}</Text> },
    { id: 'actions', header: '', cell: ({ row: { original: o } }) => (
      <RowActions actions={[{ key: 'clear', label: `Back to normal: ${name(o.logger)}`, icon: <Icon icon={RotateCcw} />, permission: CONFIG,
        onAction: () => setClearing(o) }]} />
    ) },
  ]
  return (
    <Section title="Log levels" plain level={2}>
      <Text as="p" tone="soft" size="sm">
        The server logs at {v.default_level}. Raise an area for a while to see more while chasing an error; it goes back by
        itself. The extra lines go to the server&apos;s console log only.
      </Text>
      {v.overrides.length > 0
        ? <LocalTable<LogOverride> ariaLabel="Raised log levels" columns={columns} rows={v.overrides} rowKey={(o) => o.logger || '(root)'} />
        : <Text as="p" size="sm">No area is raised.</Text>}
      <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => setRaising(true)}>Raise a log level…</ActionButton>
      <FormDialog open={raising} onOpenChange={setRaising} title="Raise a log level"
        description="More detail in the server's console log for a while. It goes back by itself.">
        <SchemaForm schema={schema} fields={fields} submitLabel="Raise"
          defaultValues={{ logger: 'app.services', level: 'DEBUG', minutes: v.default_minutes, reason: '' }}
          errorMessages={{ 'logging.invalid': 'That is not a level, an area or a time we can set.' }}
          onCancel={() => setRaising(false)} done="Log level raised" invalidates={HEALTH} after={() => setRaising(false)}
          onSubmit={(x) => raiseLogLevel({ logger: x.logger as string, level: x.level as string, minutes: x.minutes as number, reason: x.reason as string })} />
      </FormDialog>
      <ConfirmAction open={clearing !== null} onOpenChange={(o) => { if (!o) setClearing(null) }}
        title={`Back to normal: ${clearing ? name(clearing.logger) : ''}?`} confirmLabel="Back to normal" reason={{ label: 'Why' }}
        done={{ key: 'log-clear', tone: 'success', text: 'Log level back to normal' }} invalidates={HEALTH}
        onConfirm={(why) => clearLogLevel(clearing!.logger, why)} />
    </Section>
  )
}
