/** VMS Task Inbox — pending VMS tasks for the current user.
 *
 * Source is epms-api `/api/v1/tasks` (same source as Portal's unified inbox)
 * filtered to VMS document types: `vms_visit` (approval), `vms_train` (HR
 * training confirmation), `vms_ppe` (Janitor PPE confirmation). Each
 * doc_type deeplinks to the page that lets the assignee actually do the
 * thing.
 */
import { Link } from 'react-router-dom'
import { Inbox, ArrowRight, Clock, ShieldCheck, HardHat, UserCheck } from 'lucide-react'
import { useMyVmsTasks } from '@/services/api'
import { timeAgo } from '@/lib/utils'
import { groupTasks } from '@/lib/groupTasks'

function taskHref(docType: string, docId: string): string {
  if (docType === 'vms_train' || docType === 'vms_ppe') {
    return `/visitor/${docId}/compliance`
  }
  // vms_visit (and any future doc_type) → visit detail page
  return `/${docId}`
}

function taskIcon(docType: string) {
  if (docType === 'vms_train') return <ShieldCheck className="h-4 w-4 text-amber-700" />
  if (docType === 'vms_ppe')   return <HardHat     className="h-4 w-4 text-amber-700" />
  return <UserCheck className="h-4 w-4 text-primary-700" />
}

// Fixed display order + labels for VMS task groups (keyed by document_type).
const VMS_GROUP_ORDER = ['vms_visit', 'vms_train', 'vms_ppe']
const VMS_GROUP_LABELS: Record<string, string> = {
  vms_visit: 'Visit Approvals',
  vms_train: 'Training Confirmations',
  vms_ppe:   'PPE Confirmations',
}

export default function TaskInboxPage() {
  const { data, isLoading } = useMyVmsTasks()
  const tasks = data ?? []
  const groups = groupTasks(
    tasks,
    (t) => t.document_type,
    (k) => VMS_GROUP_LABELS[k] ?? k,
    VMS_GROUP_ORDER,
  )

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Task Inbox</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Visit approvals + training / PPE confirmations waiting on you.
            Same list as the Portal task inbox.
          </p>
        </div>
      </div>

      <div className="mt-6">
        {isLoading && (
          <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
            <p className="px-4 py-6 text-center text-sm text-neutral-400">Loading…</p>
          </div>
        )}

        {!isLoading && tasks.length === 0 && (
          <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
            <div className="flex flex-col items-center px-4 py-16 text-center text-sm text-neutral-400">
              <Inbox className="h-8 w-8" />
              <p className="mt-2">All caught up — no pending visit approvals.</p>
            </div>
          </div>
        )}

        {!isLoading && tasks.length > 0 && (
          <div className="flex flex-col gap-6">
            {groups.map((group) => (
              <section key={group.key}>
                <div className="mb-2 flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
                  <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
                    {group.items.length}
                  </span>
                </div>
                <ul className="divide-y divide-neutral-100 overflow-hidden rounded-lg border border-neutral-200 bg-white">
                  {group.items.map((t) => (
                    <li key={t.id}>
                      <Link
                        to={taskHref(t.document_type, t.document_id)}
                        className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-primary-50/30"
                      >
                        <div className="flex min-w-0 items-start gap-3">
                          <span className="mt-0.5 shrink-0">{taskIcon(t.document_type)}</span>
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-neutral-900">
                              {t.title || `Visit ${t.document_number}`}
                            </p>
                            <p className="mt-0.5 flex items-center gap-3 text-xs text-neutral-500">
                              <span>{t.assigned_role.replace(/_/g, ' ')}</span>
                              <span className="inline-flex items-center gap-1">
                                <Clock className="h-3 w-3" />
                                {timeAgo(t.created_at)}
                              </span>
                              {t.priority === 'urgent' && (
                                <span className="rounded-full bg-danger-50 px-1.5 py-0.5 text-[10px] font-semibold text-danger-600">
                                  URGENT
                                </span>
                              )}
                            </p>
                          </div>
                        </div>
                        <ArrowRight className="h-4 w-4 shrink-0 text-neutral-300" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
