/**
 * What the system has said about this record, and to whom.
 *
 * HSE asked that everything sent be filed against the record it concerns so the
 * trail is traceable. This is that trail. It is read-only on purpose: Safety
 * sends notices, it is not a mail client, and a compose box here would put
 * untraceable correspondence inside a statutory record.
 *
 * A message with no sent time is shown as such rather than hidden. "We meant to
 * tell them and the mail server was down" is exactly the fact somebody asks
 * about later, and a log that quietly omitted it would answer the question
 * wrongly.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Mail } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'

interface Communication {
  id: string; direction: string; from_addr: string
  to_addrs: string[]; cc_addrs: string[]
  subject: string | null; body_text: string | null
  template_key: string | null; sent_at: string | null; created_at: string
}

export function CommunicationLog({ docType, docId }: { docType: string; docId: string }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  const log = useQuery({
    queryKey: ['communications', docType, docId],
    queryFn: () => api.get<Communication[]>(
      `/api/v1/communications?doc_type=${docType}&doc_id=${docId}`),
    enabled: Boolean(docId),
    // A 403 here means this person may not see the correspondence, which is a
    // legitimate answer rather than something to retry.
    retry: false,
  })

  if (log.isLoading || log.error) return null
  const rows = log.data ?? []
  if (rows.length === 0) return null

  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Notifications sent
      </h2>
      <ul className="flex flex-col gap-1.5">
        {rows.map((c) => (
          <li key={c.id}>
            <div className="rounded-xl border border-neutral-200 bg-white p-3">
              <button type="button"
                      onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                      className="flex w-full items-start gap-2.5 text-left">
                {c.sent_at
                  ? <Mail className="mt-0.5 h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
                  : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-600"
                                   aria-hidden />}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-neutral-900">{c.subject}</span>
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    {c.to_addrs.length === 1
                      ? c.to_addrs[0]
                      : `${c.to_addrs.length} recipients`}
                    {' · '}
                    {c.sent_at
                      ? formatDateTime(c.sent_at)
                      : <span className="font-medium text-warning-700">
                          not sent — the mail server did not accept it
                        </span>}
                  </span>
                </span>
              </button>

              {expanded === c.id && (
                <div className="mt-2 border-t border-neutral-100 pt-2">
                  <p className="text-xs text-neutral-500">To: {c.to_addrs.join(', ')}</p>
                  <p className="mt-1.5 whitespace-pre-wrap text-sm text-neutral-700">
                    {c.body_text}
                  </p>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
