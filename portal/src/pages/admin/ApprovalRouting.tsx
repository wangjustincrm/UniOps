import { useMemo, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'

// ── Types ────────────────────────────────────────────────────────────────────

type Post = 'gm' | 'opm'

interface DeptRoutingRow {
  dept_id: string
  dept_code: string
  dept_name: string
  gm_or_opm: Post
  director_user_id: string | null
  supervisor_enabled: boolean
}

interface Backups {
  gm: string | null
  opm: string | null
}

interface RoutingResponse {
  departments: DeptRoutingRow[]
  backups: Backups
}

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

// ── Queries ──────────────────────────────────────────────────────────────────

function useRouting() {
  return useQuery<RoutingResponse>({
    queryKey: ['approval-routing'],
    // Reached via epms-api's server-side gateway, not a browser-direct call —
    // approval-api is server-to-server only (no browser subdomain/CORS).
    queryFn: () => epmsApi.get<RoutingResponse>('/config/approval-routing'),
  })
}

/** Fetch every user, paging until exhausted. Never rely on the default page_size=20. */
function useAllUsers() {
  return useQuery<ApiUser[]>({
    queryKey: ['approval-routing-all-users'],
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

export default function ApprovalRouting() {
  const { user } = useAuthStore()

  // UI-level gate; the backend independently enforces system_admin on PUT /routing.
  if (user?.role !== 'system_admin') {
    return <p className="p-6 text-sm text-red-600">You do not have access to Approval Routing.</p>
  }

  return (
    <PortalPageLayout activeKey="portal:/admin/approval-routing">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Approval Routing</h1>
          <p className="text-sm text-neutral-500">
            Configure which post (GM or OPM) approves each department, that department's director,
            whether its supervisor layer is on, and the GM/OPM backup approvers.
          </p>
        </div>

        <ApprovalRoutingBody />
      </div>
    </PortalPageLayout>
  )
}

// ── Body ─────────────────────────────────────────────────────────────────────

function userLabel(u: ApiUser): string {
  return `${u.full_name} (${u.email})`
}

function ApprovalRoutingBody() {
  const qc = useQueryClient()
  const routingQ = useRouting()
  const usersQ = useAllUsers()

  const [deptEdits, setDeptEdits] = useState<Record<string, Partial<DeptRoutingRow>>>({})
  const [backupEdits, setBackupEdits] = useState<Partial<Backups>>({})
  const [error, setError] = useState('')

  const save = useMutation<RoutingResponse, ApiError, RoutingResponse>({
    mutationFn: (body) => epmsApi.put<RoutingResponse>('/config/approval-routing', body),
    onSuccess: (data) => {
      qc.setQueryData(['approval-routing'], data)
      setDeptEdits({})
      setBackupEdits({})
      setError('')
    },
    onError: (e) => setError(e.message || 'Save failed'),
  })

  const activeUsers = useMemo(
    () => [...(usersQ.data ?? [])].filter((u) => u.is_active)
      .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [usersQ.data],
  )

  if (routingQ.isLoading || usersQ.isLoading) return <LoadingBlock />
  if (routingQ.isError || usersQ.isError) {
    return <ErrorBlock message="Failed to load approval routing. Please refresh and try again." />
  }

  const departments = routingQ.data?.departments ?? []
  const backups = routingQ.data?.backups ?? { gm: null, opm: null }

  const effectiveRow = (dept: DeptRoutingRow): DeptRoutingRow => ({
    ...dept,
    ...deptEdits[dept.dept_id],
  })

  const setDeptField = <K extends keyof DeptRoutingRow>(
    dept: DeptRoutingRow, field: K, value: DeptRoutingRow[K],
  ) => {
    setDeptEdits((prev) => {
      const merged = { ...(prev[dept.dept_id] ?? {}), [field]: value } as Partial<DeptRoutingRow>
      const cleaned: Partial<DeptRoutingRow> = {}
      for (const key of Object.keys(merged) as (keyof DeptRoutingRow)[]) {
        if (merged[key] !== dept[key]) (cleaned as any)[key] = merged[key]
      }
      const next = { ...prev }
      if (Object.keys(cleaned).length === 0) delete next[dept.dept_id]
      else next[dept.dept_id] = cleaned
      return next
    })
  }

  const effectiveBackups: Backups = { ...backups, ...backupEdits }

  const setBackupField = (role: Post, value: string | null) => {
    setBackupEdits((prev) => {
      const next = { ...prev, [role]: value }
      if (value === backups[role]) delete next[role]
      return next
    })
  }

  const dirtyCount = Object.keys(deptEdits).length + Object.keys(backupEdits).length

  const handleSave = () => {
    const body: RoutingResponse = {
      departments: departments.map((d) => {
        const row = effectiveRow(d)
        return {
          dept_id: row.dept_id,
          dept_code: row.dept_code,
          dept_name: row.dept_name,
          gm_or_opm: row.gm_or_opm,
          director_user_id: row.director_user_id,
          supervisor_enabled: row.supervisor_enabled,
        }
      }),
      backups: effectiveBackups,
    }
    save.mutate(body)
  }

  const handleDiscard = () => {
    setDeptEdits({})
    setBackupEdits({})
    setError('')
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <ErrorBlock message={error} />}

      <div className="overflow-x-auto rounded-xl border border-neutral-200 bg-white">
        <table className="w-full min-w-max text-sm">
          <thead className="border-b border-neutral-100 bg-neutral-50">
            <tr>
              {['Department', 'GM or OPM', 'Director', 'Supervisor'].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {departments.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-10 text-center text-sm text-neutral-400">
                  No active departments found.
                </td>
              </tr>
            ) : (
              departments.map((dept, i) => {
                const row = effectiveRow(dept)
                const isDirty = dept.dept_id in deptEdits
                return (
                  <tr
                    key={dept.dept_id}
                    className={cn('border-b border-neutral-100', i === departments.length - 1 && 'border-b-0')}
                  >
                    <td className="px-4 py-2.5 text-neutral-700">
                      <span className="font-medium">{dept.dept_code}</span>
                      <span className="text-neutral-400"> — {dept.dept_name}</span>
                      {isDirty && <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full bg-amber-400 align-middle" />}
                    </td>
                    <td className="px-4 py-2.5">
                      <select
                        value={row.gm_or_opm}
                        onChange={(e) => setDeptField(dept, 'gm_or_opm', e.target.value as Post)}
                        className="rounded-lg border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                      >
                        <option value="gm">GM</option>
                        <option value="opm">OPM</option>
                      </select>
                    </td>
                    <td className="px-4 py-2.5">
                      <select
                        value={row.director_user_id ?? ''}
                        onChange={(e) => setDeptField(dept, 'director_user_id', e.target.value || null)}
                        className="max-w-xs rounded-lg border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                      >
                        <option value="">None</option>
                        {activeUsers.map((u) => (
                          <option key={u.id} value={u.id}>{userLabel(u)}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-2.5">
                      <label className="inline-flex cursor-pointer items-center gap-2">
                        <input
                          type="checkbox"
                          checked={row.supervisor_enabled}
                          onChange={(e) => setDeptField(dept, 'supervisor_enabled', e.target.checked)}
                          className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-400"
                        />
                        <span className="text-xs text-neutral-500">{row.supervisor_enabled ? 'Enabled' : 'Disabled'}</span>
                      </label>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-neutral-700">Backup Approvers</h2>
        <p className="text-xs text-neutral-500">
          Stand-in approver used when the primary GM or OPM is unavailable.
        </p>
        <div className="flex flex-wrap gap-6">
          {(['gm', 'opm'] as const).map((role) => (
            <div key={role} className="flex flex-col gap-1">
              <label className="text-xs font-medium uppercase tracking-wide text-neutral-500">
                {role.toUpperCase()} Backup
              </label>
              <select
                value={effectiveBackups[role] ?? ''}
                onChange={(e) => setBackupField(role, e.target.value || null)}
                className="min-w-[240px] rounded-lg border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="">None</option>
                {activeUsers.map((u) => (
                  <option key={u.id} value={u.id}>{userLabel(u)}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
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
