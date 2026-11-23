import { useState } from 'react'
import { ActionButton, Badge, ConfirmAction, LocalTable, NoticeScope, Section, Text, type ColumnDef } from '@/platform-ui'
import { setFeature, type FeatureRead, type HealthRead } from './healthApi'

const CONFIG = 'sales.config'
const HEALTH = [['sales', 'health']]

/** Health → Switches (user 2026-11-23, ≈ ABP Feature management): partner writes, external email, automatic runs.
 *  The server's value is the ceiling: a person can turn one off (with a reason, audited) and back, never on past it. */
export function SwitchesSection({ health }: { health: HealthRead }) {
  return <NoticeScope><Switches health={health} /></NoticeScope>
}

function Switches({ health }: { health: HealthRead }) {
  const [changing, setChanging] = useState<{ f: FeatureRead; off: boolean } | null>(null)
  const columns: ColumnDef<FeatureRead, unknown>[] = [
    { accessorKey: 'display_name', header: 'Switch', cell: ({ row: { original: f } }) => (
      <span className="block"><Text weight="medium">{f.display_name}</Text><Text as="span" size="xs" tone="soft" className="block">{f.name}</Text></span>
    ) },
    { accessorKey: 'env', header: 'Server', cell: ({ row: { original: f } }) => (f.env ? 'On' : 'Off') },
    { accessorKey: 'stored', header: 'Turned off here', cell: ({ row: { original: f } }) => (f.stored === false ? <Badge variant="warning">Off</Badge> : '—') },
    { accessorKey: 'effective', header: 'Now', cell: ({ row: { original: f } }) => <Badge variant={f.effective ? 'success' : 'neutral'}>{f.effective ? 'On' : 'Off'}</Badge> },
    { id: 'actions', header: '', cell: ({ row: { original: f } }) => (f.stored === false
      ? <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => setChanging({ f, off: false })}>Back to the server&apos;s value</ActionButton>
      : f.env ? <ActionButton size="sm" variant="secondary" permission={CONFIG} onAction={() => setChanging({ f, off: true })}>Turn off</ActionButton> : null) },
  ]
  return (
    <Section title="Switches" plain level={2}>
      <Text as="p" tone="soft" size="sm">
        Each switch is on only when this server allows it. You can turn one off here (e.g. while a partner is down) and back;
        never on past the server.
      </Text>
      <LocalTable<FeatureRead> ariaLabel="Switches" columns={columns} rows={health.features ?? []} rowKey={(f) => f.name} />
      <ConfirmAction open={changing !== null} onOpenChange={(o) => { if (!o) setChanging(null) }} destructive={changing?.off}
        title={changing ? `${changing.off ? 'Turn off' : 'Back to the server\'s value'}: ${changing.f.display_name}?` : ''}
        description={changing?.off ? 'Nothing it guards is sent or written while it is off.' : 'It follows the server again.'}
        confirmLabel={changing?.off ? 'Turn off' : 'Back to the server\'s value'} reason={{ label: 'Why' }}
        done={{ key: 'switch', tone: 'success', text: 'Switch changed' }} invalidates={HEALTH}
        onConfirm={(why) => setFeature(changing!.f.name, changing!.off ? false : null, why)} />
    </Section>
  )
}
