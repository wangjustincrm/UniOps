/** Notification contact editor (W11).
 *
 * Two email inputs. Visits to GMP / Lab automatically trigger emails to
 * these addresses (PRD VMS-PR-021/022). Empty = no email goes out.
 *
 * Uses the "drafts overlay server data" pattern (no useEffect + setState)
 * to comply with React 19 strict mode.
 */
import { useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { Input, Button } from '@uniops/shell'
import {
  useNotificationContacts, useSetNotificationContacts,
} from '@/services/api'

export default function NotificationContactsPage() {
  const { data, isLoading } = useNotificationContacts()
  const save = useSetNotificationContacts()

  // `null` in a draft means "user hasn't touched this field yet" — display
  // server value. Anything else (including '') is an explicit user edit.
  const [draftTraining, setDraftTraining] = useState<string | null>(null)
  const [draftPpe, setDraftPpe]           = useState<string | null>(null)

  const training = draftTraining ?? data?.training_email ?? ''
  const ppe      = draftPpe ?? data?.ppe_email ?? ''

  const onSave = () => {
    save.mutate({
      training_email: training.trim() || null,
      ppe_email:      ppe.trim() || null,
    }, {
      onSuccess: () => {
        setDraftTraining(null)
        setDraftPpe(null)
      },
    })
  }

  const dirty = draftTraining !== null || draftPpe !== null

  return (
    <div className="max-w-2xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-fg">Notification contacts</h2>
          <p className="mt-0.5 text-xs text-fg-muted">
            When a visit's access area triggers training / PPE requirements
            (PRD VMS-PR-007), VMS auto-sends an email to these addresses
            after the visit is confirmed.
          </p>
        </div>
        <Button size="sm"
          onClick={onSave}
          disabled={!dirty || save.isPending}
         
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </Button>
      </div>

      {save.error && (
        <p className="mt-2 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-2 rounded-md bg-success-soft px-3 py-2 text-xs text-success-fg">
          Saved.
        </p>
      )}

      <div className="mt-5 space-y-4 rounded-lg border border-line bg-surface p-4">
        <Field
          label="Training Contact email"
          help="HR-designated. Receives a notification when a visit's access area requires food-safety training."
          value={training}
          onChange={setDraftTraining}
          loading={isLoading}
          placeholder="e.g. hr-training@royalmilk.com"
        />
        <Field
          label="PPE Contact email"
          help="Janitor-designated. Receives a notification when a visit's access area requires PPE issuance."
          value={ppe}
          onChange={setDraftPpe}
          loading={isLoading}
          placeholder="e.g. ppe-issuance@royalmilk.com"
        />
      </div>

      <p className="mt-4 text-xs text-fg-muted">
        Leave a field blank to disable that channel. Emails are sent through the
        same SMTP configuration as EPMS / OA (configured in the Portal Admin Panel).
      </p>
    </div>
  )
}

function Field({
  label, help, value, onChange, loading, placeholder,
}: {
  label: string; help: string
  value: string; onChange: (v: string) => void
  loading: boolean; placeholder: string
}) {
  return (
    <label className="block text-sm">
      <span className="block font-medium text-fg-secondary">{label}</span>
      <Input
        type="email"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading}
        placeholder={placeholder}
        className="mt-1 w-full"
      />
      <span className="mt-1 block text-xs text-fg-muted">{help}</span>
    </label>
  )
}
