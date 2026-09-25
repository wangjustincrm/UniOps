/**
 * What I have been trained on, and the certificates for it.
 *
 * ★ This page exists because the certificate did not have a way in. The
 * endpoint that issues one lets the person it belongs to fetch it, but every
 * training screen is behind `ehs.training.read`, which an ordinary employee
 * does not hold — so the document was generated for somebody who could not
 * reach it. That is the same shape as the permit signatures a supervisor could
 * write and not read back, and it is the third time this module has built
 * something correct and unreachable.
 *
 * Deliberately not the register with a filter on it: the register is a
 * different screen for a different job, and the permission that guards it is
 * the reason this page is needed.
 */
import { useQuery } from '@tanstack/react-query'
import { GraduationCap } from 'lucide-react'
import { api, useFileOpener } from '@/lib/api'
import { Card, Empty, LoadFailedNote, PageHeader } from '@/components/ui'
import { formatDate } from '@/lib/formatDate'
import { daysOverdue } from '@/lib/formatDate'

interface Record_ {
  id: string
  course_label: string
  completed_on: string
  expires_on: string | null
  delivery: string
  certificate_file_id: string | null
  user_name: string
}

export default function MyTrainingPage() {
  const records = useQuery({
    queryKey: ['my-training'],
    queryFn: () => api.get<Record_[]>('/api/v1/training/records/mine'),
  })
  const opener = useFileOpener()

  const rows = records.data ?? []

  return (
    <div className="p-4">
      <PageHeader
        title="My training"
        subtitle="What you have completed, and the certificate for each."
      />

      {records.isError && <LoadFailedNote what="Your training record" />}
      {opener.error && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {opener.error}
        </p>
      )}

      {!records.isLoading && rows.length === 0 && !records.isError && (
        <Empty>
          Nothing is on your training record yet. It fills in as you complete
          courses — ask HSE if you think something is missing.
        </Empty>
      )}

      <ul className="flex flex-col gap-2">
        {rows.map((r) => {
          const lapsed = r.expires_on ? (daysOverdue(r.expires_on) ?? 0) > 0 : false
          return (
            <li key={r.id}>
              <Card>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <GraduationCap className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1 text-sm font-semibold text-neutral-900">
                    {r.course_label}
                  </span>
                  {lapsed && (
                    <span className="rounded-full bg-danger-50 px-2.5 py-0.5 text-xs font-semibold text-danger-700">
                      Expired
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  Completed {formatDate(r.completed_on)}
                  {r.expires_on && ` · ${lapsed ? 'expired' : 'valid until'} ${formatDate(r.expires_on)}`}
                  {r.delivery === 'external' && ' · taken elsewhere'}
                </p>
                <div className="mt-2">
                  {r.delivery === 'external' ? (
                    r.certificate_file_id ? (
                      <button type="button" disabled={opener.busy}
                              onClick={() => opener.open(
                                `/api/v1/training/records/${r.id}/certificate`,
                                `certificate-${r.course_label}.pdf`)}
                              className="text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                        {opener.busy ? 'Opening…' : 'Open the certificate you gave us'}
                      </button>
                    ) : (
                      <span className="text-xs text-neutral-500">
                        No certificate on file for this one.
                      </span>
                    )
                  ) : (
                    <button type="button" disabled={opener.busy}
                            onClick={() => opener.open(
                              `/api/v1/training/records/${r.id}/certificate/issued`,
                              `certificate-${r.course_label}.pdf`)}
                            className="text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                      {opener.busy ? 'Opening…' : 'Certificate'}
                    </button>
                  )}
                </div>
              </Card>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
