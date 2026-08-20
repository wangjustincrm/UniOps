import { useMemo, useState } from 'react'
import { Lock, Loader2, Search, AlertCircle, CheckCircle2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'

// ── Types ────────────────────────────────────────────────────────────────────

interface AuthzRole {
  code: string
  label: string
  sort: number
  is_active: boolean
  /**
   * False for ADDITIONAL-ONLY roles (erp_pa_officer / payment_officer): granted
   * under Additional Roles, never as someone's primary login role. Comes from
   * identity's `role_defs.assignable_as_primary`, so adding another such role is
   * a data change — no frontend edit. Optional so an identity older than
   * migration 0009 keeps the pre-existing behaviour (everything selectable).
   */
  assignable_as_primary?: boolean
}

interface AuthzPermission {
  key: string
  module: string
  label: string
  sort: number
  locked_for: string[]
}

interface AuthzDefs {
  roles: AuthzRole[]
  permissions: AuthzPermission[]
}

/** role_code → { permission_key: bool }. Legacy shape used by GET/PATCH /config/role-permissions. */
type Matrix = Record<string, Record<string, boolean>>

/**
 * Permission keys hidden from the matrix because they are coupled to another,
 * visible checkbox and granted automatically by the backend. `mdm.vendor.write`
 * is granted whenever the EPMS "Vendor Master" row is ticked (identity
 * patch_matrix couples them), so showing it as a separate toggle would let an
 * admin desync the two. See
 * docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md
 */
const HIDDEN_PERMISSION_KEYS = new Set<string>(['mdm.vendor.write'])

interface ApiUser {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
}

interface UserListResponse {
  items: ApiUser[]
  total: number
}

/** Error thrown by epmsApi carries the raw `detail` payload (see lib/api.ts). */
type ApiError = Error & { detail?: unknown; status?: number }

const USERS_PAGE_SIZE = 200

// ── Shared queries ───────────────────────────────────────────────────────────

function useAuthzDefs() {
  return useQuery<AuthzDefs>({
    queryKey: ['authz-defs'],
    queryFn: () => epmsApi.get<AuthzDefs>('/config/authz-defs'),
  })
}

function useAuthzMatrix() {
  return useQuery<Matrix>({
    queryKey: ['authz-matrix'],
    queryFn: () => epmsApi.get<Matrix>('/config/role-permissions'),
  })
}

/** user_id (string UUID) → additional role codes. From GET /config/user-roles. */
function useUserRoles() {
  return useQuery<{ user_roles: Record<string, string[]> }>({
    queryKey: ['authz-user-roles'],
    queryFn: () => epmsApi.get<{ user_roles: Record<string, string[]> }>('/config/user-roles'),
  })
}

/** Fetch every user, paging until exhausted. Never rely on the default page_size=20. */
function useAllUsers() {
  return useQuery<ApiUser[]>({
    queryKey: ['authz-all-users'],
    queryFn: async () => {
      const all: ApiUser[] = []
      let page = 1
      for (;;) {
        const res = await epmsApi.get<UserListResponse>(`/users?page=${page}&page_size=${USERS_PAGE_SIZE}`)
        all.push(...res.items)
        if (res.items.length < USERS_PAGE_SIZE) break
        page += 1
      }
      return all
    },
  })
}

// ── Shared UI bits ───────────────────────────────────────────────────────────

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-neutral-400">
      <Loader2 className="h-4 w-4 animate-spin" />Loading…
    </div>
  )
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
      <AlertCircle className="h-4 w-4 shrink-0" />{message}
    </div>
  )
}

// ── Page shell ───────────────────────────────────────────────────────────────

export default function AccessControl() {
  const { user } = useAuthStore()
  const [tab, setTab] = useState<'matrix' | 'users'>('matrix')

  // UI-level gate; the backend independently enforces system_admin on the
  // underlying identity authz endpoints.
  if (user?.role !== 'system_admin') {
    return <p className="p-6 text-sm text-red-600">You do not have access to Access Control.</p>
  }

  return (
    <PortalPageLayout activeKey="portal:/admin/access-control">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Access Control</h1>
          <p className="text-sm text-neutral-500">
            Manage the role permission matrix and per-user role assignments across every module.
          </p>
        </div>

        <div className="flex gap-1 border-b border-neutral-200">
          {(['matrix', 'users'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                '-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors',
                tab === t ? 'border-primary-600 text-primary-700' : 'border-transparent text-neutral-500 hover:text-neutral-700',
              )}
            >
              {t === 'matrix' ? 'Permission Matrix' : 'User Roles'}
            </button>
          ))}
        </div>

        {tab === 'matrix' ? <PermissionMatrixTab /> : <UserRolesTab />}
      </div>
    </PortalPageLayout>
  )
}

// ── Tab 1: Permission Matrix ─────────────────────────────────────────────────

/** Dirty cell keyed by `${role}.${permissionKey}` → the new (unsaved) value. */
type DirtyMap = Record<string, boolean>

function splitCellKey(cellKey: string): [string, string] {
  const idx = cellKey.indexOf('.')
  return [cellKey.slice(0, idx), cellKey.slice(idx + 1)]
}

function PermissionMatrixTab() {
  const qc = useQueryClient()
  const defsQ = useAuthzDefs()
  const matrixQ = useAuthzMatrix()
  const [dirty, setDirty] = useState<DirtyMap>({})
  const [error, setError] = useState('')
  const [lockedCells, setLockedCells] = useState<{ role: string; key: string }[] | null>(null)

  const save = useMutation<Matrix, ApiError, Matrix>({
    mutationFn: (changes) => epmsApi.patch<Matrix>('/config/role-permissions', changes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['authz-matrix'] })
      setDirty({})
      setError('')
      setLockedCells(null)
    },
    onError: (e) => {
      const detail = e.detail as { locked?: { role: string; key: string }[] } | undefined
      if (detail && Array.isArray(detail.locked)) {
        setLockedCells(detail.locked)
        setError('Some of your changes were rejected because those cells are locked.')
      } else {
        setLockedCells(null)
        setError(e.message || 'Save failed')
      }
    },
  })

  const roles = useMemo(
    () => [...(defsQ.data?.roles ?? [])].sort((a, b) => a.sort - b.sort),
    [defsQ.data],
  )
  const permissions = useMemo(
    () => [...(defsQ.data?.permissions ?? [])]
      .filter((p) => !HIDDEN_PERMISSION_KEYS.has(p.key))
      .sort((a, b) => a.sort - b.sort),
    [defsQ.data],
  )

  // Group permissions by module, preserving the backend-defined sort order.
  const groups = useMemo(() => {
    const out: { module: string; perms: AuthzPermission[] }[] = []
    for (const p of permissions) {
      const last = out[out.length - 1]
      if (last && last.module === p.module) last.perms.push(p)
      else out.push({ module: p.module, perms: [p] })
    }
    return out
  }, [permissions])

  if (defsQ.isLoading || matrixQ.isLoading) return <LoadingBlock />
  if (defsQ.isError || matrixQ.isError) {
    return <ErrorBlock message="Failed to load the permission matrix. Please refresh and try again." />
  }

  const matrix = matrixQ.data ?? {}

  const cellValue = (role: string, key: string): boolean => {
    const cellKey = `${role}.${key}`
    if (cellKey in dirty) return dirty[cellKey]
    return matrix[role]?.[key] ?? false
  }

  const isLocked = (role: string, perm: AuthzPermission) => perm.locked_for.includes(role)

  const toggleCell = (role: string, perm: AuthzPermission) => {
    if (isLocked(role, perm)) return
    const cellKey = `${role}.${perm.key}`
    const nextVal = !cellValue(role, perm.key)
    const baseline = matrix[role]?.[perm.key] ?? false
    setDirty((prev) => {
      const next = { ...prev }
      if (nextVal === baseline) delete next[cellKey]
      else next[cellKey] = nextVal
      return next
    })
  }

  const dirtyCount = Object.keys(dirty).length

  const handleSave = () => {
    const changes: Matrix = {}
    for (const cellKey of Object.keys(dirty)) {
      const [role, key] = splitCellKey(cellKey)
      changes[role] = changes[role] ?? {}
      changes[role][key] = dirty[cellKey]
    }
    save.mutate(changes)
  }

  const handleDiscard = () => {
    setDirty({})
    setError('')
    setLockedCells(null)
  }

  const cellIsLockedError = (role: string, key: string) =>
    !!lockedCells?.some((l) => l.role === role && l.key === key)

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <div className="flex flex-col gap-1 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <span className="flex items-center gap-2"><AlertCircle className="h-4 w-4 shrink-0" />{error}</span>
          {lockedCells && lockedCells.length > 0 && (
            <ul className="ml-6 list-disc text-xs">
              {lockedCells.map((l) => (
                <li key={`${l.role}.${l.key}`}>{l.role} · {l.key}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white">
        <table className="w-full min-w-max text-sm">
          <thead className="border-b border-neutral-100 bg-neutral-50">
            <tr>
              <th className="sticky left-0 bg-neutral-50 px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">
                Permission
              </th>
              {roles.map((r) => (
                <th key={r.code} className="whitespace-nowrap px-3 py-3 text-center text-xs font-medium uppercase tracking-wide text-neutral-500">
                  {r.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <FragmentGroup key={group.module} module={group.module}>
                {group.perms.map((perm, i) => (
                  <tr
                    key={perm.key}
                    className={cn(
                      'border-b border-neutral-100',
                      i === group.perms.length - 1 && 'border-b-0',
                    )}
                  >
                    <td className="sticky left-0 whitespace-nowrap bg-white px-4 py-2.5 text-neutral-700">
                      {perm.label}
                    </td>
                    {roles.map((r) => {
                      const locked = isLocked(r.code, perm)
                      const rejected = cellIsLockedError(r.code, perm.key)
                      const cellKey = `${r.code}.${perm.key}`
                      return (
                        <td key={r.code} className={cn('px-3 py-2.5 text-center', rejected && 'bg-red-50')}>
                          <label
                            className={cn('inline-flex items-center justify-center gap-1', locked ? 'cursor-not-allowed' : 'cursor-pointer')}
                            title={locked ? 'Locked for this role' : undefined}
                          >
                            <input
                              type="checkbox"
                              checked={cellValue(r.code, perm.key)}
                              disabled={locked}
                              onChange={() => toggleCell(r.code, perm)}
                              className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-70"
                            />
                            {locked && <Lock className="h-3 w-3 text-neutral-400" />}
                            {cellKey in dirty && !locked && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />}
                          </label>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </FragmentGroup>
            ))}
          </tbody>
        </table>
      </div>

      {dirtyCount > 0 && (
        <div className="sticky bottom-4 flex items-center gap-3 self-start rounded-xl border border-neutral-200 bg-white px-4 py-3 shadow-lg">
          <span className="text-sm font-medium text-neutral-700">{dirtyCount} unsaved change{dirtyCount === 1 ? '' : 's'}</span>
          <button
            onClick={handleDiscard}
            disabled={save.isPending}
            className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-60"
          >
            Discard
          </button>
          <button
            onClick={handleSave}
            disabled={save.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </button>
        </div>
      )}
    </div>
  )
}

/** Renders a module group header row followed by its permission rows. */
function FragmentGroup({ module, children }: { module: string; children: React.ReactNode }) {
  return (
    <>
      <tr>
        <td colSpan={999} className="sticky left-0 bg-neutral-100 px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          {module.toUpperCase()}
        </td>
      </tr>
      {children}
    </>
  )
}

// ── Tab 2: User Roles ────────────────────────────────────────────────────────

interface RowEdit {
  primary: string
  additional: string[]
  touched: boolean
}

function UserRolesTab() {
  const qc = useQueryClient()
  const defsQ = useAuthzDefs()
  const usersQ = useAllUsers()
  const userRolesQ = useUserRoles()
  const [filter, setFilter] = useState('')
  const [edits, setEdits] = useState<Record<string, RowEdit>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [rowMsg, setRowMsg] = useState<Record<string, { ok: boolean; msg: string } | undefined>>({})

  const activeRoles = useMemo(
    () => [...(defsQ.data?.roles ?? [])].filter((r) => r.is_active).sort((a, b) => a.sort - b.sort),
    [defsQ.data],
  )
  // Additional-only roles are still offered as Additional Roles chips — they are
  // only removed from the Primary Role dropdown. The backend rejects them as a
  // primary role too (identity put_user_roles), this just keeps the admin from
  // picking something that would 422.
  const primaryRoles = useMemo(
    () => activeRoles.filter((r) => r.assignable_as_primary !== false),
    [activeRoles],
  )

  const rowFor = (u: ApiUser): RowEdit =>
    edits[u.id] ?? { primary: u.role, additional: userRolesQ.data?.user_roles[u.id] ?? [], touched: false }

  const setPrimary = (u: ApiUser, primary: string) => {
    setEdits((prev) => {
      const row = rowFor(u)
      return { ...prev, [u.id]: { primary, additional: row.additional.filter((a) => a !== primary), touched: true } }
    })
  }

  const toggleAdditional = (u: ApiUser, role: string) => {
    setEdits((prev) => {
      const row = rowFor(u)
      const additional = row.additional.includes(role)
        ? row.additional.filter((a) => a !== role)
        : [...row.additional, role]
      return { ...prev, [u.id]: { ...row, additional, touched: true } }
    })
  }

  const handleSave = async (u: ApiUser) => {
    const row = rowFor(u)
    setSaving((s) => ({ ...s, [u.id]: true }))
    setRowMsg((m) => ({ ...m, [u.id]: undefined }))
    try {
      await epmsApi.put(`/config/users/${u.id}/roles`, { primary: row.primary, additional: row.additional })
      setRowMsg((m) => ({ ...m, [u.id]: { ok: true, msg: 'Saved' } }))
      setEdits((prev) => {
        const next = { ...prev }
        delete next[u.id]
        return next
      })
      qc.invalidateQueries({ queryKey: ['authz-all-users'] })
      qc.invalidateQueries({ queryKey: ['authz-user-roles'] })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Save failed'
      setRowMsg((m) => ({ ...m, [u.id]: { ok: false, msg } }))
    } finally {
      setSaving((s) => ({ ...s, [u.id]: false }))
    }
  }

  const filtered = useMemo(() => {
    const all = usersQ.data ?? []
    const q = filter.trim().toLowerCase()
    if (!q) return all
    return all.filter((u) => u.full_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q))
  }, [usersQ.data, filter])

  if (defsQ.isLoading || usersQ.isLoading || userRolesQ.isLoading) return <LoadingBlock />
  if (defsQ.isError || usersQ.isError || userRolesQ.isError) {
    return <ErrorBlock message="Failed to load users. Please refresh and try again." />
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 max-w-xs">
        <Search className="h-4 w-4 shrink-0 text-neutral-400" />
        <input
          className="flex-1 text-sm focus:outline-none"
          placeholder="Search name or email…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white">
        {filtered.length === 0 ? (
          <div className="py-10 text-center text-sm text-neutral-400">No users found.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Name / Email', 'Primary Role', 'Additional Roles', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((u, i) => {
                const row = rowFor(u)
                const msg = rowMsg[u.id]
                const isSaving = !!saving[u.id]
                // Guard against a primary role no longer in the active list (e.g. deactivated after assignment).
                // A user already holding a hidden/inactive role as primary (deactivated
                // after assignment, or an additional-only role set before the backend
                // guard existed) still needs their current value in the list — otherwise
                // the <select> would silently display someone else's role.
                const primaryOptions = primaryRoles.some((r) => r.code === row.primary)
                  ? primaryRoles
                  : [{ code: row.primary, label: `${row.primary} (not selectable)`, sort: -1, is_active: false }, ...primaryRoles]
                const additionalOptions = activeRoles.filter((r) => r.code !== row.primary)

                return (
                  <tr key={u.id} className={cn('border-b border-neutral-100 align-top', i === filtered.length - 1 && 'border-b-0')}>
                    <td className="px-4 py-3">
                      <p className="font-medium text-neutral-800">{u.full_name}</p>
                      <p className="text-[11px] text-neutral-400">{u.email}</p>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={row.primary}
                        onChange={(e) => setPrimary(u, e.target.value)}
                        className="rounded-lg border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                      >
                        {primaryOptions.map((r) => (
                          <option key={r.code} value={r.code}>{r.label}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex max-w-sm flex-wrap gap-1.5">
                        {additionalOptions.map((r) => {
                          const on = row.additional.includes(r.code)
                          return (
                            <button
                              key={r.code}
                              type="button"
                              onClick={() => toggleAdditional(u, r.code)}
                              className={cn(
                                'rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors',
                                on
                                  ? 'border-primary-400 bg-primary-50 text-primary-700'
                                  : 'border-neutral-200 text-neutral-500 hover:bg-neutral-50',
                              )}
                            >
                              {r.label}
                            </button>
                          )
                        })}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleSave(u)}
                          disabled={!row.touched || isSaving}
                          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-40 transition-colors"
                        >
                          {isSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                          Save
                        </button>
                        {msg && (
                          <span className={cn('flex items-center gap-1 text-xs', msg.ok ? 'text-green-700' : 'text-red-600')}>
                            {msg.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
                            {msg.msg}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
