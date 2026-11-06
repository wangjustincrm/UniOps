/**
 * Who is emailed before certificates expire, and how far ahead.
 *
 * Round 8 (open box): "Create a new rule for managing training certificates
 * that are about to expire. This rule will allow notifications to be emailed
 * to a specified email address and have a field for the number of days
 * notice." One email per rule lists every certificate that has come inside
 * its window and has not been listed to that rule before — training records
 * with an expiry date and the certifications on worker profiles.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import {
  Card, Empty, ErrorNote, Field, inputClass, LoadFailedNote, PageHeader, primaryButton,
  secondaryButton,
} from '@/components/ui'

interface Rule { id: string; email: string; days_before: number; is_active: boolean }

const KEY = ['expiry-rules']

export default function ExpiryNoticesPage() {
  const rules = useQuery({
    queryKey: KEY,
    queryFn: () => api.get<Rule[]>('/api/v1/training/expiry-rules'),
  })
  const [adding, setAdding] = useState(false)
  return (
    <div className="p-4">
      <PageHeader title="Training notifications"
                  subtitle="Email an address before training certificates expire.">
        {!adding && (
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a rule
          </button>
        )}
      </PageHeader>
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        Checked on every scan. Each certificate is listed once to each rule; one renewed to a
        new date is listed again when that date comes near. A new rule’s first email lists
        everything already inside its window. Someone who has since retaken the course, or
        who has left, is not listed.
      </p>
      {adding && <NewRule onClose={() => setAdding(false)} />}
      {rules.isError && <LoadFailedNote what="The rules" />}
      {rules.isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      {rules.data?.length === 0 && !adding && (
        <Empty>No rules yet. Add one to have an address emailed before certificates expire.</Empty>
      )}
      <ul className="flex flex-col gap-2">
        {(rules.data ?? []).map((r) => <RuleRow key={r.id} rule={r} />)}
      </ul>
    </div>
  )
}

function NewRule({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [days, setDays] = useState('32')
  const create = useMutation({
    mutationFn: () => api.post('/api/v1/training/expiry-rules', {
      email: email.trim(), days_before: Number(days),
    }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: KEY }); onClose() },
  })
  const n = Number(days)
  const usable = email.trim().includes('@') && Number.isInteger(n) && n >= 1 && n <= 365
  return (
    <Card className="mb-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Email to">
          <input className={inputClass} type="email" value={email} autoFocus
                 onChange={(e) => setEmail(e.target.value)} placeholder="hse@example.com" />
        </Field>
        <Field label="Days of notice" hint="1 to 365">
          <input className={inputClass} type="number" min={1} max={365} value={days}
                 onChange={(e) => setDays(e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton} disabled={!usable || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
    </Card>
  )
}

function RuleRow({ rule }: { rule: Rule }) {
  const queryClient = useQueryClient()
  const done = () => queryClient.invalidateQueries({ queryKey: KEY })
  const patch = useMutation({
    mutationFn: (body: Partial<Rule>) => api.patch(`/api/v1/training/expiry-rules/${rule.id}`, body),
    onSuccess: done,
  })
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/training/expiry-rules/${rule.id}`),
    onSuccess: done,
  })
  const busy = patch.isPending || remove.isPending
  // ★ Review (uniops-8b): the number of days could be set only when adding a
  // rule; changing it meant removing the rule and adding it again.
  const [days, setDays] = useState<string | null>(null)
  const daysValid = days !== null && /^\d+$/.test(days) && Number(days) >= 1 && Number(days) <= 365
  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="min-w-0 flex-1">
            <span className="block break-all text-sm font-semibold text-neutral-900">{rule.email}</span>
            {days === null ? (
              <span className="mt-0.5 block text-xs text-neutral-500">
                {rule.days_before} day{rule.days_before === 1 ? '' : 's'} before a certificate expires
                {!rule.is_active && ' · switched off'}{' · '}
                <button type="button" className="font-semibold text-primary-700 hover:underline"
                        onClick={() => setDays(String(rule.days_before))}>
                  Change
                </button>
              </span>
            ) : (
              <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-neutral-600">
                <input type="number" min={1} max={365} value={days} aria-label="Days before"
                       onChange={(e) => setDays(e.target.value)} className={`${inputClass} w-24`} />
                days before
                <button type="button" className={secondaryButton} disabled={busy}
                        onClick={() => setDays(null)}>Cancel</button>
                <button type="button" className={primaryButton} disabled={!daysValid || busy}
                        onClick={() => {
                          if (!busy && daysValid) {
                            patch.mutate({ days_before: Number(days) }, { onSuccess: () => setDays(null) })
                          }
                        }}>
                  Save
                </button>
              </span>
            )}
          </span>
          <label className="flex min-h-[44px] items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={rule.is_active} disabled={busy}
                   onChange={(e) => { if (!busy) patch.mutate({ is_active: e.target.checked }) }} />
            On
          </label>
          <button type="button" disabled={busy} aria-label={`Remove the rule for ${rule.email}`}
                  onClick={() => {
                    if (!busy && window.confirm(`Stop emailing ${rule.email}?`)) remove.mutate()
                  }}
                  className="rounded p-2 text-neutral-400 hover:bg-danger-50 hover:text-danger-600 disabled:opacity-40">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
        <ErrorNote error={patch.error ?? remove.error} />
      </Card>
    </li>
  )
}
