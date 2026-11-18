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
import { Button, Checkbox, Input, MenuItem, Modal, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
import {
  useCreateZone, useDeleteZone, useUpdateZone, useUserDirectory, useZones,
  type UserBrief, type Zone,
} from '@/services/api'

const inputCls =
  'mt-1 w-full rounded-md border border-line-strong px-3 py-1.5 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500'

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
          <h2 className="text-base font-semibold text-fg">Access zones</h2>
          <p className="mt-0.5 max-w-3xl text-xs text-fg-muted">
            The zones a visit can be booked for. A zone with <span className="font-medium">zone managers</span> needs
            approval — any one of its managers approves at the “Zone Manager” step of the VMS visit
            workflow (Portal → Admin → Approval Workflows). A zone with <span className="font-medium">no managers
            needs no approval</span>: its visits are confirmed when booked. <span className="font-medium">GMP-grade</span> zones
            need a passing health declaration before the badge prints, training / PPE confirmation at
            check-in, and count in the GMP reports.
          </p>
        </div>
        <Button size="md" className="shrink-0" onClick={() => setEditing('new')}>
          <Plus className="h-4 w-4" /> Add zone
        </Button>
      </div>

      {(error || actionError) && (
        <p className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          {(error ?? actionError)?.message}
        </p>
      )}

      <div className="mt-4 overflow-x-auto rounded-md border border-line bg-surface">
        <Table density="compact">
          <THead>
            <Tr>
              <Th>Zone</Th>
              <Th>GMP-grade</Th>
              <Th>Zone managers (approval)</Th>
              <Th>Visits</Th>
              <Th>Status</Th>
              <Th />
            </Tr>
          </THead>
          <TBody>
            {isLoading && (
              <Tr><Td colSpan={6} className="text-xs text-fg-muted">Loading…</Td></Tr>
            )}
            {zones?.map((z) => (
              <Tr key={z.id} className={`border-t border-line-subtle ${z.is_active ? '' : 'text-fg-muted'}`}>
                <Td>
                  <p className="font-medium">{z.name}</p>
                  <p className="font-mono text-2xs text-fg-muted">{z.code}</p>
                </Td>
                <Td>{z.gmp_grade ? 'Yes' : '—'}</Td>
                <Td>
                  {z.managers.length === 0
                    ? <span className="text-xs text-fg-muted">None — no approval needed</span>
                    : (
                      <div className="flex flex-wrap gap-1">
                        {z.managers.map((m) => (
                          <span key={m.id} title={m.email}
                            className={`rounded-full px-2 py-0.5 text-xs ${m.is_active ? 'bg-primary-50 text-primary-700' : 'bg-danger-soft text-danger-fg line-through'}`}>
                            {m.name}
                          </span>
                        ))}
                        {z.managers.every((m) => !m.is_active) && (
                          <span className="text-xs text-danger-fg">No active manager — visits cannot be submitted</span>
                        )}
                      </div>
                    )}
                </Td>
                <Td className="text-fg-secondary">{z.visit_count}</Td>
                <Td>{z.is_active ? 'Active' : 'Retired'}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    <Button variant="subtle" size="icon-xs" onClick={() => setEditing(z)} disabled={busy} title="Edit" aria-label="Edit"
                     >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="subtle" size="xs"
                      disabled={busy}
                      onClick={() => update.mutate({ id: z.id, is_active: !z.is_active })}
                     >
                      {z.is_active ? 'Retire' : 'Reactivate'}
                    </Button>
                    {z.visit_count === 0 && (
                      <Button variant="subtle" size="icon-xs" aria-label="Delete zone"
                        disabled={busy}
                        title="Delete (only zones no visit has used)"
                        onClick={() => { if (confirm(`Delete zone “${z.name}”?`)) remove.mutate(z.id) }}
                        className="text-danger-fg hover:bg-danger-soft">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
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
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      title={zone ? `Edit zone — ${zone.name}` : 'Add zone'}
      // Closes by Cancel / the close button only (no Escape, no backdrop), as before.
      closeOnEscape={false}
      size="full"
      className="max-w-lg"
    >
      <div>
        {zone && (
          <p className="mt-1 text-xs text-fg-muted">
            Manager changes apply to visits submitted from now on. A visit already waiting for approval
            stays with the managers it was sent to.
          </p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-xs font-medium text-fg-secondary sm:col-span-2">Name
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className={inputCls} />
          </label>
          <label className="text-xs font-medium text-fg-secondary">Order
            <Input type="number" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} className={inputCls} />
          </label>
        </div>
        <label className="mt-3 inline-flex items-center gap-2 text-sm text-fg-secondary">
          <Checkbox checked={gmp} onChange={(e) => setGmp(e.target.checked)} />
          GMP-grade (health declaration, training / PPE at check-in, GMP reports)
        </label>

        <p className="mt-4 text-xs font-medium text-fg-secondary">Zone managers</p>
        <p className="text-2xs text-fg-muted">Leave empty if visits to this zone need no approval.</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {managers.map((m) => (
            <span key={m.id} title={m.email} className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-700">
              {m.name}
              <Button variant="subtle" size="icon-xs" onClick={() => setManagers(managers.filter((x) => x.id !== m.id))} aria-label={`Remove ${m.name}`}>
                <X className="h-3 w-3" />
              </Button>
            </span>
          ))}
          {managers.length === 0 && <span className="text-xs text-fg-muted">None — no approval needed</span>}
        </div>
        <div className="relative mt-2">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-fg-muted" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Add a manager — search by name or email (≥ 2 chars)…"
            className="w-full pl-8 pr-3"
          />
        </div>
        {query.length >= 2 && (
          <div className="mt-1 max-h-48 overflow-y-auto rounded-md border border-line bg-surface">
            {dir.isLoading && <p className="px-3 py-2 text-xs text-fg-muted">Searching…</p>}
            {!dir.isLoading && (dir.data?.items.length ?? 0) === 0 && (
              <p className="px-3 py-2 text-xs text-fg-muted">No matches.</p>
            )}
            {dir.data?.items.map((u: UserBrief) => (
              <MenuItem key={u.id} onClick={() => add(u)}
                disabled={managers.some((m) => m.id === u.id)}
                className="block border-b border-line-subtle disabled:text-fg-muted disabled:opacity-100">
                <p className="font-medium">{u.full_name}</p>
                <p className="text-xs text-fg-muted">{u.email}{u.department_name && ` · ${u.department_name}`}</p>
              </MenuItem>
            ))}
          </div>
        )}

        {save.error && <p className="mt-3 text-xs text-danger-fg">{save.error.message}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" size="md" onClick={onClose} disabled={save.isPending}>Cancel</Button>
          <Button size="md" onClick={onSave} disabled={save.isPending || !name.trim()}>
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {zone ? 'Save changes' : 'Add zone'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
