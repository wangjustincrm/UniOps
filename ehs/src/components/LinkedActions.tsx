/**
 * The corrective actions raised from a record, shown on that record.
 *
 * ★ Round 4, TC-14: "any root cause analysis, or capa completed as part of the
 * drill or on any form shall display the capa description, and the information
 * in the history of changes."
 *
 * A drill showed its root cause and nothing about what was being done about it.
 * A submitted form said "3 failed check(s) raised an action" and did not say
 * which, so the only way to the work was to guess at the actions list and match
 * by title. In both places the action existed, was owned, and had a due date
 * that nobody on that screen could see.
 *
 * The description is here because a title is a label and the description is the
 * instruction — it is what somebody reading a root cause needs in order to
 * judge whether the cause has been answered. The change history stays on the
 * action itself, which is why every row opens it.
 */
import { useQuery } from '@tanstack/react-query'
import { ClipboardList } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { LoadFailedNote, SectionTitle } from '@/components/ui'

interface LinkedAction {
  id: string
  action_no: string
  title: string
  description: string | null
  status: string
  owner_name: string | null
  due_date: string
}

const TONE: Record<string, string> = {
  closed: 'bg-success-50 text-success-700',
  verified: 'bg-success-50 text-success-700',
  in_progress: 'bg-warning-50 text-warning-700',
  open: 'bg-neutral-100 text-neutral-600',
}

export function LinkedActions({
  sourceType, sourceId, emptyNote, title = 'Corrective actions',
}: {
  sourceType: string
  sourceId: string
  /** What "none" should say here, in the reader's own terms. */
  emptyNote: string
  title?: string
}) {
  const actions = useQuery({
    queryKey: ['actions', sourceType, sourceId],
    queryFn: () => api.get<LinkedAction[]>(
      `/api/v1/actions?source_type=${sourceType}&source_id=${sourceId}`),
    enabled: Boolean(sourceId),
  })

  if (actions.isLoading) {
    return <p className="mt-3 text-xs text-neutral-500">Loading…</p>
  }
  // ★ A failed read is not "nothing was raised". On this screen those are
  // opposite claims: one says the record is answered for, the other says it is
  // not, and a corrective action nobody can see is the fault this module keeps
  // finding.
  if (actions.isError) {
    return <LoadFailedNote what="The corrective actions" />
  }

  const rows = actions.data ?? []
  if (rows.length === 0) {
    return <p className="mt-3 text-xs text-neutral-500">{emptyNote}</p>
  }

  return (
    <div className="mt-3">
      <SectionTitle className="mb-1.5 flex items-center gap-1.5">
        <ClipboardList className="h-3.5 w-3.5 text-neutral-400" aria-hidden />
        {title}
      </SectionTitle>
      <ul className="flex flex-col gap-1.5">
        {rows.map((a) => (
          <li key={a.id}>
            <Link
              to={`/actions/${a.id}`}
              className="block rounded-lg border border-neutral-200 bg-white px-2.5 py-2 text-xs hover:border-primary-300 hover:bg-primary-50/40">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-neutral-500">{a.action_no}</span>
                <span className="min-w-0 flex-1 font-medium text-neutral-900">{a.title}</span>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${
                  TONE[a.status] ?? 'bg-neutral-100 text-neutral-600'}`}>
                  {a.status.replace('_', ' ')}
                </span>
              </span>
              {a.description && (
                <span className="mt-1 block whitespace-pre-wrap text-neutral-600">
                  {a.description}
                </span>
              )}
              <span className="mt-1 block text-[11px] text-neutral-400">
                {a.owner_name ?? 'Nobody named'} · due {formatDate(a.due_date)} ·
                {' '}open it for who changed what, and when
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
