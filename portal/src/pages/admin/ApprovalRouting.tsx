import { useMemo, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SortTh, useSortableRows, Checkbox, Select, TBody, THead, Table, Td, Tr, Button } from '@uniops/shell'
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
  // Which post approves a payment application that covers purchase orders from
  // SEVERAL departments. The per-department mapping above cannot answer that —
  // one of them may route to GM and another to OPM — so this one setting
  // decides for all such payments. Their Department Manager and Director steps
  // are skipped (visibly, with a reason) and this post approves instead.
  cross_department_gm_or_opm: Post
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
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-fg-muted">
      <Loader2 className="h-4 w-4 animate-spin" />Loading…
    </div>
  )
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">
      <AlertCircle className="h-4 w-4 shrink-0" />{message}
    </div>
  )
}

// ── Page shell ───────────────────────────────────────────────────────────────

export default function ApprovalRouting() {
  const { user } = useAuthStore()

  // UI-level gate; the backend independently enforces system_admin on PUT /routing.
  if (user?.role !== 'system_admin') {
    return <p className="p-6 text-sm text-danger-fg">You do not have access to Approval Routing.</p>
  }

  return (
    <PortalPageLayout activeKey="portal:/admin/approval-routing">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Approval Routing</h1>
          <p className="text-sm text-fg-muted">
            Configure which post (GM or OPM) approves each department, that department's director,
            whether its supervisor layer is on, who approves payments spanning several departments,
            and the GM/OPM backup approvers.
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
  const [crossDeptEdit, setCrossDeptEdit] = useState<Post | null>(null)
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
  // Whole list, sorted here. Rows are keyed by dept_id and edits live in
  // deptEdits[dept_id], so reordering never moves an edit to another row. The
  // getters read the SAVED values, so a row does not jump while it is edited.
  const usersById = useMemo(() => new Map((usersQ.data ?? []).map((u) => [u.id, u])), [usersQ.data])
  const sortedDepts = useSortableRows(routingQ.data?.departments, {
    department: (d) => `${d.dept_code} — ${d.dept_name}`,
    gm_or_opm: (d) => d.gm_or_opm.toUpperCase(),
    director: (d) => {
      const u = d.director_user_id ? usersById.get(d.director_user_id) : undefined
      return u ? userLabel(u) : null
    },
    supervisor: (d) => (d.supervisor_enabled ? 'Enabled' : 'Disabled'),
  })

  if (routingQ.isLoading || usersQ.isLoading) return <LoadingBlock />
  if (routingQ.isError || usersQ.isError) {
    return <ErrorBlock message="Failed to load approval routing. Please refresh and try again." />
  }

  const departments = routingQ.data?.departments ?? []
  const backups = routingQ.data?.backups ?? { gm: null, opm: null }
  const crossDept: Post = routingQ.data?.cross_department_gm_or_opm ?? 'gm'
  const effectiveCrossDept: Post = crossDeptEdit ?? crossDept

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
    + (crossDeptEdit !== null && crossDeptEdit !== crossDept ? 1 : 0)

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
      cross_department_gm_or_opm: effectiveCrossDept,
    }
    save.mutate(body)
  }

  const handleDiscard = () => {
    setDeptEdits({})
    setBackupEdits({})
    setCrossDeptEdit(null)
    setError('')
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <ErrorBlock message={error} />}

      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <Table density="compact" className="min-w-max">
          <THead>
            <Tr>
              <SortTh label="Department" sortKey="department" sort={sortedDepts.sort} onSort={sortedDepts.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="GM or OPM" sortKey="gm_or_opm" sort={sortedDepts.sort} onSort={sortedDepts.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="Director" sortKey="director" sort={sortedDepts.sort} onSort={sortedDepts.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="Supervisor" sortKey="supervisor" sort={sortedDepts.sort} onSort={sortedDepts.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
            </Tr>
          </THead>
          <TBody>
            {departments.length === 0 ? (
              <Tr>
                <Td align="center" colSpan={4} className="text-sm text-fg-muted">
                  No active departments found.
                </Td>
              </Tr>
            ) : (
              sortedDepts.rows.map((dept, i) => {
                const row = effectiveRow(dept)
                const isDirty = dept.dept_id in deptEdits
                return (
                  <Tr
                    key={dept.dept_id}
                    className={cn('border-b border-line-subtle', i === departments.length - 1 && 'border-b-0')}
                  >
                    <Td className="text-fg-secondary">
                      <span className="font-medium">{dept.dept_code}</span>
                      <span className="text-fg-muted"> — {dept.dept_name}</span>
                      {isDirty && <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full bg-warning-400 align-middle" />}
                    </Td>
                    <Td>
                      <Select
                        value={row.gm_or_opm}
                        onChange={(e) => setDeptField(dept, 'gm_or_opm', e.target.value as Post)}
                       
                      >
                        <option value="gm">GM</option>
                        <option value="opm">OPM</option>
                      </Select>
                    </Td>
                    <Td>
                      <Select
                        value={row.director_user_id ?? ''}
                        onChange={(e) => setDeptField(dept, 'director_user_id', e.target.value || null)}
                        className="max-w-xs"
                      >
                        <option value="">None</option>
                        {activeUsers.map((u) => (
                          <option key={u.id} value={u.id}>{userLabel(u)}</option>
                        ))}
                      </Select>
                    </Td>
                    <Td>
                      <label className="inline-flex cursor-pointer items-center gap-2">
                        <Checkbox
                         
                          checked={row.supervisor_enabled}
                          onChange={(e) => setDeptField(dept, 'supervisor_enabled', e.target.checked)}
                         
                        />
                        <span className="text-xs text-fg-muted">{row.supervisor_enabled ? 'Enabled' : 'Disabled'}</span>
                      </label>
                    </Td>
                  </Tr>
                )
              })
            )}
          </TBody>
        </Table>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-fg-secondary">Cross-Department Payments</h2>
        <p className="text-xs text-fg-muted">
          A vendor invoice often covers purchase orders from several departments, and such a
          payment is raised as one document. The table above cannot say who approves it — its
          departments may point at different posts — so this setting decides. Those payments
          skip the Department Manager and Director steps (shown struck through on the approval
          timeline, with the reason) and are approved here instead.
        </p>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-fg-muted">
            Approved by
          </label>
          <Select
            value={effectiveCrossDept}
            onChange={(e) => setCrossDeptEdit(e.target.value as Post)}
            className="min-w-[240px] max-w-[240px]"
          >
            <option value="gm">GM</option>
            <option value="opm">OPM</option>
          </Select>
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-fg-secondary">Backup Approvers</h2>
        <p className="text-xs text-fg-muted">
          Stand-in approver used when the primary GM or OPM is unavailable.
        </p>
        <div className="flex flex-wrap gap-6">
          {(['gm', 'opm'] as const).map((role) => (
            <div key={role} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-muted">
                {role.toUpperCase()} Backup
              </label>
              <Select
                value={effectiveBackups[role] ?? ''}
                onChange={(e) => setBackupField(role, e.target.value || null)}
                className="min-w-[240px]"
              >
                <option value="">None</option>
                {activeUsers.map((u) => (
                  <option key={u.id} value={u.id}>{userLabel(u)}</option>
                ))}
              </Select>
            </div>
          ))}
        </div>
      </div>

      {dirtyCount > 0 && (
        <div className="sticky bottom-4 flex items-center gap-3 self-start rounded-xl border border-line bg-surface px-4 py-3 shadow-lg">
          <span className="text-sm font-medium text-fg-secondary">{dirtyCount} unsaved change{dirtyCount === 1 ? '' : 's'}</span>
          <Button variant="secondary" size="sm"
            onClick={handleDiscard}
            disabled={save.isPending}
           
          >
            Discard
          </Button>
          <Button size="sm"
            onClick={handleSave}
            disabled={save.isPending}
           
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </Button>
        </div>
      )}
    </div>
  )
}
