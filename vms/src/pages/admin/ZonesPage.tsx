/** Access zones editor (replaced the Quality Manager roster, 2026-10).
 *
 * Each zone is something a visit can be booked for. Per zone:
 *  - GMP-grade → health declaration before the badge prints, training / PPE
 *    confirmation at check-in, counted in the GMP / Lab reports;
 *  - managers → who approves at the "Zone Manager" step of the VMS approval
 *    workflow (any one of them). No managers = the visit needs no approval.
 *
 * A zone's code never changes (visits keep it). A zone that visits use can be
 * retired, not deleted.
 */
import { useState } from 'react'
import { Loader2, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import {
  useCreateZone, useDeleteZone, useUpdateZone, useUserDirectory, useZones,
  type UserBrief, type Zone,
} from '@/services/api'

const btnCls =
  'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50'
const inputCls =
  'mt-1 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

export default function ZonesPage() {
  const { data: zones, isLoading, error } = useZones()
  const update = useUpdateZone()
  const remove = useDeleteZone()
  const [editing, setEditing] = useState<Zone | 'new' | null>(null)
  const busy = update.isPending || remove.isPending
  const actionError = update.error ?? remove.error

  return (
    <div className="max-w-5xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-neutral-900">Access zones</h2>
          <p className="mt-0.5 max-w-3xl text-xs text-neutral-500">
            The zones a visit can be booked for. A zone with <span className="font-medium">zone managers</span> needs
            approval — any one of its managers approves at the “Zone Manager” step of the VMS visit
            workflow (Portal → Admin → Approval Workflows). A zone with <span className="font-medium">no managers
            needs no approval</span>: its visits are confirmed when booked. <span className="font-medium">GMP-grade</span> zones
            need a passing health declaration before the badge prints, training / PPE confirmation at
            check-in, and count in the GMP reports.
          </p>
        </div>
        <button onClick={() => setEditing('new')} className={`${btnCls} shrink-0 bg-primary-600 text-white hover:bg-primary-700`}>
          <Plus className="h-4 w-4" /> Add zone
        </button>
      </div>

      {(error || actionError) && (
        <p className="mt-3 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          {(error ?? actionError)?.message}
        </p>
      )}

      <div className="mt-4 overflow-x-auto rounded-md border border-neutral-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="px-3 py-2">Zone</th>
              <th className="px-3 py-2">GMP-grade</th>
              <th className="px-3 py-2">Zone managers (approval)</th>
              <th className="px-3 py-2">Visits</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-4 text-xs text-neutral-400">Loading…</td></tr>
            )}
            {zones?.map((z) => (
              <tr key={z.id} className={`border-t border-neutral-100 ${z.is_active ? '' : 'text-neutral-400'}`}>
                <td className="px-3 py-2">
                  <p className="font-medium">{z.name}</p>
                  <p className="font-mono text-[11px] text-neutral-400">{z.code}</p>
                </td>
                <td className="px-3 py-2">{z.gmp_grade ? 'Yes' : '—'}</td>
                <td className="px-3 py-2">
                  {z.managers.length === 0
                    ? <span className="text-xs text-neutral-400">None — no approval needed</span>
                    : (
                      <div className="flex flex-wrap gap-1">
                        {z.managers.map((m) => (
                          <span key={m.id} title={m.email}
                            className={`rounded-full px-2 py-0.5 text-xs ${m.is_active ? 'bg-primary-50 text-primary-700' : 'bg-danger-50 text-danger-600 line-through'}`}>
                            {m.name}
                          </span>
                        ))}
                        {z.managers.every((m) => !m.is_active) && (
                          <span className="text-xs text-danger-600">No active manager — visits cannot be submitted</span>
                        )}
                      </div>
                    )}
                </td>
                <td className="px-3 py-2 text-neutral-600">{z.visit_count}</td>
                <td className="px-3 py-2">{z.is_active ? 'Active' : 'Retired'}</td>
                <td className="px-3 py-2">
                  <div className="flex justify-end gap-1">
                    <button onClick={() => setEditing(z)} disabled={busy} title="Edit"
                      className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-40">
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => update.mutate({ id: z.id, is_active: !z.is_active })}
                      className="rounded px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-100 disabled:opacity-40">
                      {z.is_active ? 'Retire' : 'Reactivate'}
                    </button>
                    {z.visit_count === 0 && (
                      <button
                        disabled={busy}
                        title="Delete (only zones no visit has used)"
                        onClick={() => { if (confirm(`Delete zone “${z.name}”?`)) remove.mutate(z.id) }}
                        className="rounded p-1 text-danger-600 hover:bg-danger-50 disabled:opacity-40">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <ZoneModal zone={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />
      )}
    </div>
  )
}

// ── Add / edit modal ─────────────────────────────────────────────────────────

function ZoneModal({ zone, onClose }: { zone: Zone | null; onClose: () => void }) {
  const create = useCreateZone()
  const update = useUpdateZone()
  const save = zone ? update : create

  const [name, setName] = useState(zone?.name ?? '')
  const [gmp, setGmp] = useState(zone?.gmp_grade ?? false)
  const [sortOrder, setSortOrder] = useState(String(zone?.sort_order ?? 100))
  const [managers, setManagers] = useState<{ id: string; name: string; email: string }[]>(
    zone?.managers.filter((m) => m.is_active).map(({ id, name, email }) => ({ id, name, email })) ?? [],
  )
  const [query, setQuery] = useState('')
  const dir = useUserDirectory(query)

  const add = (u: UserBrief) => {
    if (!managers.some((m) => m.id === u.id)) {
      setManagers([...managers, { id: u.id, name: u.full_name, email: u.email }])
    }
    setQuery('')
  }

  const onSave = () => {
    const body = {
      name: name.trim(),
      gmp_grade: gmp,
      sort_order: Number.parseInt(sortOrder, 10) || 0,
      manager_ids: managers.map((m) => m.id),
    }
    if (zone) update.mutate({ id: zone.id, ...body }, { onSuccess: onClose })
    else create.mutate(body, { onSuccess: onClose })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-neutral-900">{zone ? `Edit zone — ${zone.name}` : 'Add zone'}</h3>
        {zone && (
          <p className="mt-1 text-xs text-neutral-500">
            Manager changes apply to visits submitted from now on. A visit already waiting for approval
            stays with the managers it was sent to.
          </p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-xs font-medium text-neutral-600 sm:col-span-2">Name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className={inputCls} />
          </label>
          <label className="text-xs font-medium text-neutral-600">Order
            <input type="number" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} className={inputCls} />
          </label>
        </div>
        <label className="mt-3 inline-flex items-center gap-2 text-sm text-neutral-700">
          <input type="checkbox" checked={gmp} onChange={(e) => setGmp(e.target.checked)} />
          GMP-grade (health declaration, training / PPE at check-in, GMP reports)
        </label>

        <p className="mt-4 text-xs font-medium text-neutral-600">Zone managers</p>
        <p className="text-[11px] text-neutral-500">Leave empty if visits to this zone need no approval.</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {managers.map((m) => (
            <span key={m.id} title={m.email} className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-700">
              {m.name}
              <button onClick={() => setManagers(managers.filter((x) => x.id !== m.id))} aria-label={`Remove ${m.name}`}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          {managers.length === 0 && <span className="text-xs text-neutral-400">None — no approval needed</span>}
        </div>
        <div className="relative mt-2">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-neutral-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Add a manager — search by name or email (≥ 2 chars)…"
            className="w-full rounded-md border border-neutral-300 bg-white py-2 pl-8 pr-3 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          />
        </div>
        {query.length >= 2 && (
          <div className="mt-1 max-h-48 overflow-y-auto rounded-md border border-neutral-200 bg-white">
            {dir.isLoading && <p className="px-3 py-2 text-xs text-neutral-500">Searching…</p>}
            {!dir.isLoading && (dir.data?.items.length ?? 0) === 0 && (
              <p className="px-3 py-2 text-xs text-neutral-500">No matches.</p>
            )}
            {dir.data?.items.map((u: UserBrief) => (
              <button key={u.id} type="button" onClick={() => add(u)}
                disabled={managers.some((m) => m.id === u.id)}
                className="block w-full border-b border-neutral-100 px-3 py-2 text-left text-sm hover:bg-primary-50/40 disabled:text-neutral-400">
                <p className="font-medium">{u.full_name}</p>
                <p className="text-xs text-neutral-500">{u.email}{u.department_name && ` · ${u.department_name}`}</p>
              </button>
            ))}
          </div>
        )}

        {save.error && <p className="mt-3 text-xs text-danger-600">{save.error.message}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} disabled={save.isPending} className={`${btnCls} border border-neutral-300`}>Cancel</button>
          <button onClick={onSave} disabled={save.isPending || !name.trim()}
            className={`${btnCls} bg-primary-600 text-white hover:bg-primary-700`}>
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {zone ? 'Save changes' : 'Add zone'}
          </button>
        </div>
      </div>
    </div>
  )
}
