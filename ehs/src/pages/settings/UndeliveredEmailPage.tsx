/**
 * Email that was filed but never left, and sending it again.
 *
 * ★ Review (uniops-8b): such messages were listed by the server and nowhere
 * on screen, and nothing ever retried one — a mail server that refused, none
 * set up, or the service stopping between saving and sending. Sent again by
 * hand on purpose: once the mail server is fixed, a timer would send a
 * fortnight of stale notices at once, and HSE is who knows which still matter.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import {
  Card, Empty, ErrorNote, LoadFailedNote, PageHeader, secondaryButton,
} from '@/components/ui'

interface Message {
  id: string; to_addrs: string[]; cc_addrs: string[]; subject: string | null
  template_key: string | null; created_at: string
}

const KEY = ['communications', 'undelivered']

export default function UndeliveredEmailPage() {
  const rows = useQuery({
    queryKey: KEY,
    queryFn: () => api.get<Message[]>('/api/v1/communications/undelivered'),
  })
  return (
    <div className="p-4">
      <PageHeader title="Undelivered email"
                  subtitle="Messages Safety filed but could not send, newest first." />
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        A run of these usually means the mail server under Admin → Company Settings is
        missing or wrong. Fix that first, then send again the ones that still matter —
        each is sent at most once.
      </p>
      {rows.isError && <LoadFailedNote what="The undelivered messages" />}
      {rows.isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      {rows.data?.length === 0 && <Empty>Nothing waiting: every message filed has been sent.</Empty>}
      <ul className="flex flex-col gap-2">
        {(rows.data ?? []).map((m) => <Row key={m.id} message={m} />)}
      </ul>
    </div>
  )
}

function Row({ message }: { message: Message }) {
  const queryClient = useQueryClient()
  const send = useMutation({
    mutationFn: () => api.post<{ sent: boolean; message: string }>(
      `/api/v1/communications/${message.id}/send-again`),
    onSuccess: (r) => { if (r.sent) queryClient.invalidateQueries({ queryKey: KEY }) },
  })
  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-neutral-900">{message.subject || '(no subject)'}</p>
            <p className="mt-0.5 text-xs text-neutral-500">
              To {[...message.to_addrs, ...message.cc_addrs].join(', ') || '—'} · filed{' '}
              {formatDateTime(message.created_at)}
            </p>
          </div>
          <button type="button" className={secondaryButton} disabled={send.isPending}
                  onClick={() => { if (!send.isPending) send.mutate() }}>
            {send.isPending ? 'Sending…' : 'Send again'}
          </button>
        </div>
        {send.data && !send.data.sent && (
          <p className="mt-2 text-xs text-warning-700">{send.data.message}</p>
        )}
        <ErrorNote error={send.error} />
      </Card>
    </li>
  )
}
