import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Badge, CrudPage, DetailList, ErrorMessage, formatDateTime, queryKeys, type ColumnDef, type FieldSpec,
} from '@/platform-ui'
import { historyTab } from '../historyTab'
import {
  CHANNELS, fetchEvents, fetchRuleAudit, fetchUsers, rulesClient, sendTest,
  type NotificationRuleCreate, type NotificationRuleRead, type NotificationRuleUpdate,
} from './notificationsApi'

const CHANNEL_LABELS = { TASK: 'Task Inbox task', EMAIL: 'Email', BOTH: 'Task and email' } as const
// One address per line (or separated by commas); sent as a list.
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const addresses = z.string().transform((s) => s.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean))
  .refine((list) => list.every((a) => EMAIL.test(a)), 'Not an email address')
const base = { channel: z.enum(CHANNELS), users: z.array(z.string()), emails: addresses, active: z.boolean() }
const createSchema = z.object({ event_type: z.string().min(1, 'Choose the event'), ...base })
const updateSchema = z.object(base)

const columns: ColumnDef<NotificationRuleRead, unknown>[] = [
  { accessorKey: 'event_type', header: 'Event', cell: ({ row: { original: r } }) => (
    <span className="flex flex-col">
      <span>{r.event_label}</span>
      <span className="text-xs text-neutral-500">{r.kind === 'action' ? 'needs someone to act: always a task' : 'notify only'}{r.gate ? ' · go-live gate' : ''}</span>
    </span>
  ) },
  { accessorKey: 'channel', header: 'Channel', cell: ({ row }) => CHANNEL_LABELS[row.original.channel] },
  { id: 'recipients', header: 'Recipients', cell: ({ row: { original: r } }) =>
    [r.users.length && `${r.users.length} user${r.users.length > 1 ? 's' : ''}`, r.emails.length && r.emails.join(', ')]
      .filter(Boolean).join(' · ') || 'the operator' },
  { accessorKey: 'tested_at', header: 'Last test', cell: ({ row }) => (row.original.tested_at ? formatDateTime(row.original.tested_at) : '—') },
  { accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge> },
]

const errorMessages = {
  'notification.event_taken': 'This event already has a rule: edit that one.',
  'notification.no_recipients': 'An email rule needs at least one user or address.',
  'notification.unknown_user': 'One of the users is not an active user.',
  'notification.inactive': 'Activate the rule before testing it.',
}

/** "Send test" (AC-OB-71): through the rule as it is; recorded on the rule. */
function TestRule({ rule }: { rule: NotificationRuleRead }) {
  const qc = useQueryClient()
  const m = useMutation({ mutationFn: () => sendTest(rule.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.list(rulesClient) }) })
  return (
    <div className="flex flex-col gap-2 text-sm">
      <DetailList items={[
        ['Last test', rule.tested_at ? formatDateTime(rule.tested_at) : 'never'],
        ['Result', m.data?.result ?? rule.test_result],
      ]} />
      <span>
        <ActionButton size="sm" variant="secondary" permission="sales.config" disabled={!rule.active}
          onAction={() => m.mutateAsync().catch(() => {})}>Send test notification</ActionButton>
      </span>
      <ErrorMessage error={m.error} messages={errorMessages} />
    </div>
  )
}

/** Configuration → Notifications (FR-OB-56): per event, who is told and how.
 *  With no rule an event's task goes to the operator and no email is sent
 *  (AC-OB-52). */
export default function NotificationRulesPage() {
  const events = useQuery({ queryKey: ['sales', 'notification-events'], queryFn: fetchEvents, staleTime: 60_000 })
  const users = useQuery({ queryKey: ['sales', 'notification-users'], queryFn: () => fetchUsers(), staleTime: 300_000 })
  const fields: FieldSpec[] = [
    { name: 'event_type', label: 'Event', type: 'select', required: true,
      options: (events.data ?? []).filter((e) => e.rule_id === null).map((e) => ({ value: e.key, label: e.label })) },
    { name: 'channel', label: 'Channel', type: 'select', required: true,
      options: CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABELS[c] })),
      hint: 'An event that needs someone to act always raises its task; the channel adds email.' },
    { name: 'users', label: 'Users', type: 'multiselect',
      options: (users.data ?? []).map((u) => ({ value: u.id, label: `${u.name} (${u.email})` })),
      hint: 'The first user gets the task (none: the operator); every user gets the email.' },
    { name: 'emails', label: 'Other email addresses', type: 'textarea',
      hint: 'One per line — e.g. the warehouse, for a cancellation after the 940.' },
    { name: 'active', label: 'Active', type: 'checkbox' },
  ]
  return (
    <CrudPage<NotificationRuleRead, NotificationRuleCreate, NotificationRuleUpdate>
      title="Notifications" singular="notification rule"
      description="Who hears about each event, and how. Without a rule, the task goes to the operator and no email is sent. Emails are only sent where SALES_EXTERNAL_EMAIL_ENABLED is on."
      client={rulesClient} columns={columns}
      writePermission="sales.config" createSchema={createSchema} updateSchema={updateSchema} fields={fields}
      immutableOnEdit={['event_type']}
      emptyValues={{ event_type: '', channel: 'TASK', users: [], emails: '', active: true }}
      toFormValues={(r) => ({ ...r, emails: r.emails.join('\n') })}
      errorMessages={errorMessages}
      recordTitle={(r) => r.event_label}
      recordSubtitle={(r) => `${r.event_type} · ${r.channel}`}
      detailTabs={(r, details) => [details, { id: 'test', label: 'Send a test', render: () => <TestRule rule={r} /> },
        historyTab('notification-rules', r.id, r.version, fetchRuleAudit)]}
    />
  )
}
