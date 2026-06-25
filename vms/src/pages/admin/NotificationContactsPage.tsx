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
          <h2 className="text-base font-semibold text-neutral-900">Notification contacts</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            When a visit's access area triggers training / PPE requirements
            (PRD VMS-PR-007), VMS auto-sends an email to these addresses
            after the visit is confirmed.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {save.error && (
        <p className="mt-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-2 rounded-md bg-success-50 px-3 py-2 text-xs text-success-600">
          Saved.
        </p>
      )}

      <div className="mt-5 space-y-4 rounded-lg border border-neutral-200 bg-white p-4">
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

      <p className="mt-4 text-xs text-neutral-500">
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
      <span className="block font-medium text-neutral-700">{label}</span>
      <input
        type="email"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading}
        placeholder={placeholder}
        className="mt-1 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 disabled:opacity-50"
      />
      <span className="mt-1 block text-xs text-neutral-500">{help}</span>
    </label>
  )
}
