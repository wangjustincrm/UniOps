/**
 * What has been changed on this record, by whom, and when.
 *
 * The backing log is written at the ORM flush for every table in this module,
 * so this panel works on any record without the page it sits on knowing
 * anything about which fields exist — pass the table and the id.
 *
 * Values from the medical tables are absent by design rather than missing:
 * copying an allergy history into a general log would put it behind a weaker
 * gate than the medical table exists to provide. The field, the person and the
 * time are still there, which is what "was this altered, by whom, when"
 * actually needs.
 *
 * ★ The trail is read with `ehs.incident.read` — the auditor's key. The panel
 * was offered to everyone, so a worker on their own action or an assessed
 * hazard pressed it and got an error (B3/B4 review L1). It is shown to those
 * who may read it.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'

interface Change {
  id: string
  action: string
  field: string | null
  old_value: string | null
  new_value: string | null
  changed_by_name: string | null
  changed_at: string
  row_ref: string | null
}

/** `first_aid_note` reads as a column name; "First aid note" reads as English. */
function fieldLabel(field: string | null): string {
  if (!field) return ''
  const words = field.replace(/_id$/, '').replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function ChangeHistory({ table, rowId }: { table: string; rowId: string }) {
  const { can } = usePermissions()
  if (!can('ehs.incident.read')) return null
  return <Trail table={table} rowId={rowId} />
}

function Trail({ table, rowId }: { table: string; rowId: string }) {
  const [open, setOpen] = useState(false)

  const changes = useQuery({
    queryKey: ['change-log', table, rowId],
    // Not fetched until somebody asks for it: most people opening a record
    // are not auditing it, and this is the longest list on the page.
    enabled: open,
    queryFn: () => api.get<Change[]>(
      `/api/v1/change-log?table=${encodeURIComponent(table)}&row_id=${rowId}`),
  })

  return (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex min-h-[32px] items-center gap-1.5 text-xs font-semibold text-neutral-500 hover:text-neutral-900"
      >
        <History className="h-3.5 w-3.5" aria-hidden />
        {open ? 'Hide the history' : 'History of changes'}
      </button>

      {open && (
        <div className="mt-2 rounded-xl border border-neutral-200 bg-white p-3.5">
          {changes.isLoading && <p className="text-xs text-neutral-500">Loading…</p>}
          {changes.isError && (
            <p className="text-xs text-neutral-500">
              The history could not be read. It is kept either way — nothing is
              lost by this panel failing.
            </p>
          )}
          {changes.data?.length === 0 && (
            <p className="text-xs text-neutral-500">
              Nothing recorded against this yet.
            </p>
          )}

          <ul className="flex flex-col gap-2">
            {changes.data?.map((c) => (
              <li key={c.id} className="text-xs">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-neutral-800">
                    {c.action === 'insert' ? 'Created'
                      : c.action === 'delete' ? 'Removed'
                      : fieldLabel(c.field)}
                  </span>
                  <span className="text-neutral-500">
                    {c.changed_by_name ?? 'Unknown'} · {formatDateTime(c.changed_at)}
                  </span>
                </span>

                {c.action === 'update' && (
                  <span className="mt-0.5 block text-neutral-600">
                    {c.old_value === null && c.new_value === null ? (
                      // Either it was genuinely empty on both sides, or the
                      // value is one this log does not keep. Saying which
                      // would mean guessing; saying "changed" is true either way.
                      <span className="text-neutral-400">changed</span>
                    ) : (
                      <>
                        <span className="text-neutral-400">
                          {c.old_value ?? 'empty'}
                        </span>
                        <span className="px-1 text-neutral-300">→</span>
                        <span>{c.new_value ?? 'empty'}</span>
                      </>
                    )}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
