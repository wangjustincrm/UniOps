import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, ChevronDown, Loader2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, FormField, Input, MenuItem, SortTh, TBody, THead, Table, Td, Th, Tr, useSortableRows, Card } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { epmsApi } from '@/lib/api'
import { adminApi, type DelegationCreate, type DelegationOut, type DelegationUpdate } from '@/services/adminApi'
import { cn, formatDate, todayISO } from '@/lib/utils'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { useConfirmDialog } from '@/components/ConfirmDialog'

// ── Types ────────────────────────────────────────────────────────────────────

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

const USERS_PAGE_SIZE = 200

type DelegationStatus = 'Scheduled' | 'Active' | 'Ended' | 'Revoked'

type FormMode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; row: DelegationOut }

interface FormState {
  delegatorId: string
  delegateId: string
  startDate: string
  endDate: string
  note: string
}

const EMPTY_FORM: FormState = { delegatorId: '', delegateId: '', startDate: '', endDate: '', note: '' }

const STATUS_VARIANT: Record<DelegationStatus, 'info' | 'success' | 'neutral' | 'danger'> = {
  Scheduled: 'info', Active: 'success', Ended: 'neutral', Revoked: 'danger',
}

// ── Queries ──────────────────────────────────────────────────────────────────

function useDelegations() {
  return useQuery<DelegationOut[]>({
    queryKey: ['approval-delegations'],
    queryFn: () => adminApi.listDelegations(),
  })
}

/** Fetch every user, paging until exhausted. Never rely on the default page_size=20 —
 * see ApprovalRouting.tsx's identical helper; kept as a local copy since there is no
 * shared "list all users" utility in this codebase yet. */
function useAllUsers() {
  return useQuery<ApiUser[]>({
    queryKey: ['approval-delegation-all-users'],
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

// ── Date helpers ─────────────────────────────────────────────────────────────

/** Today's calendar date at the plant (company time zone), as YYYY-MM-DD.
 * Used only to derive status client-side; the server is the real authority on
 * "active". */
function todayIso(): string {
  return todayISO()
}

/** Mirrors the server's status derivation: revoked wins, then the inclusive window. */
function deriveStatus(row: DelegationOut, today: string): DelegationStatus {
  if (row.revoked_at) return 'Revoked'
  if (today < row.start_date) return 'Scheduled'
  if (today > row.end_date) return 'Ended'
  return 'Active'
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
    <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{message}
    </div>
  )
}

function userLabel(u: ApiUser): string {
  return `${u.full_name} (${u.email})`
}

// ── User picker — escapes overflow via a body portal, fixed-positioned ─────────

interface UserComboboxProps {
  users: ApiUser[]
  value: string
  onChange: (id: string) => void
  placeholder: string
}

function UserCombobox({ users, value, onChange, placeholder }: UserComboboxProps) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const selected = users.find((u) => u.id === value) ?? null

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return
      if (popRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const pool = needle
      ? users.filter((u) => u.full_name.toLowerCase().includes(needle) || u.email.toLowerCase().includes(needle))
      : users
    return pool.slice(0, 50)
  }, [users, q])

  const toggle = () => {
    setRect(triggerRef.current?.getBoundingClientRect() ?? null)
    setOpen((o) => !o)
  }

  return (
    <>
      <Button variant="secondary" size="md"
        ref={triggerRef} type="button" onClick={toggle}
        className="w-full justify-between"
      >
        <span className="truncate">
          {selected ? userLabel(selected) : <span className="text-fg-muted">{placeholder}</span>}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-fg-muted" />
      </Button>
      {open && rect && createPortal(
        <div
          ref={popRef}
          style={{ position: 'fixed', top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 260) }}
          className="z-[60] rounded-lg border border-line bg-surface shadow-lg"
        >
          <Input inputSize="sm"
            autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or email…"
            className="m-2 w-[calc(100%-1rem)]"
          />
          <ul className="max-h-64 overflow-y-auto pb-1">
            {filtered.map((u) => (
              <li key={u.id}>
                <MenuItem
                  onClick={() => { onChange(u.id); setOpen(false); setQ('') }}
                  className="block truncate py-1.5"
                >
                  {userLabel(u)}
                </MenuItem>
              </li>
            ))}
            {filtered.length === 0 && <li className="px-3 py-2 text-xs text-fg-muted">No matches</li>}
          </ul>
        </div>,
        document.body,
      )}
    </>
  )
}

// ── Page shell ───────────────────────────────────────────────────────────────

export default function ApprovalDelegation() {
  const { user } = useAuthStore()

  // UI-level gate; the backend independently enforces system_admin on every
  // /approval/v1/delegations verb (and epms-api's gateway passes that through).
  if (user?.role !== 'system_admin') {
    return <p className="p-6 text-sm text-danger-fg">You do not have access to Approval Delegation.</p>
  }

  return (
    <PortalPageLayout activeKey="portal:/admin/approval-delegation">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Approval Delegation</h1>
          <p className="text-sm text-fg-muted">
            Name a dated stand-in who can approve on someone's behalf. The delegate can act on the
            delegator's approval tasks for the selected date range; both retain the right to approve.
          </p>
        </div>

        <ApprovalDelegationBody />
      </div>
    </PortalPageLayout>
  )
}

// ── Body ─────────────────────────────────────────────────────────────────────

function ApprovalDelegationBody() {
  const qc = useQueryClient()
  const delegationsQ = useDelegations()
  const usersQ = useAllUsers()

  const [mode, setMode] = useState<FormMode>({ kind: 'closed' })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [formError, setFormError] = useState('')
  const dlg = useConfirmDialog()

  const activeUsers = useMemo(
    () => [...(usersQ.data ?? [])].filter((u) => u.is_active)
      .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [usersQ.data],
  )
  const usersById = useMemo(() => {
    const m = new Map<string, ApiUser>()
    for (const u of usersQ.data ?? []) m.set(u.id, u)
    return m
  }, [usersQ.data])

  // Whole list, sorted here; opens newest start date first, as before.
  const today = todayIso()
  const delegatorNameOf = (row: DelegationOut) =>
    row.delegator_name ?? usersById.get(row.delegator_user_id)?.full_name ?? null
  const delegateNameOf = (row: DelegationOut) =>
    row.delegate_name ?? usersById.get(row.delegate_user_id)?.full_name ?? null
  const sorted = useSortableRows<DelegationOut, 'delegator' | 'delegate' | 'start_date' | 'end_date' | 'note' | 'status'>(
    delegationsQ.data, {
    delegator: delegatorNameOf,
    delegate: delegateNameOf,
    start_date: (r) => r.start_date,
    end_date: (r) => r.end_date,
    note: (r) => r.note,
    status: (r) => deriveStatus(r, today),
  }, { key: 'start_date', dir: 'desc' })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['approval-delegations'] })

  const closeForm = () => { setMode({ kind: 'closed' }); setForm(EMPTY_FORM); setFormError('') }

  const openCreate = () => { setForm(EMPTY_FORM); setFormError(''); setMode({ kind: 'create' }) }

  const openEdit = (row: DelegationOut) => {
    setForm({
      delegatorId: row.delegator_user_id,
      delegateId: row.delegate_user_id,
      startDate: row.start_date,
      endDate: row.end_date,
      note: row.note ?? '',
    })
    setFormError('')
    setMode({ kind: 'edit', row })
  }

  const create = useMutation<DelegationOut, Error, DelegationCreate>({
    mutationFn: (body) => adminApi.createDelegation(body),
    onSuccess: () => { invalidate(); closeForm() },
    // Rendered inline on the form (below), not as a toast: a 409 overlap means
    // the fix is to pick different dates, so the message must stay next to them.
    onError: (e) => setFormError(e.message || 'Failed to create delegation.'),
  })

  const update = useMutation<DelegationOut, Error, { id: string; body: DelegationUpdate }>({
    mutationFn: ({ id, body }) => adminApi.updateDelegation(id, body),
    onSuccess: () => { invalidate(); closeForm() },
    onError: (e) => setFormError(e.message || 'Failed to update delegation.'),
  })

  const revoke = useMutation<DelegationOut, Error, string>({
    mutationFn: (id) => adminApi.revokeDelegation(id),
    onSuccess: invalidate,
  })

  const handleRevoke = async (row: DelegationOut) => {
    const delegateName = row.delegate_name ?? usersById.get(row.delegate_user_id)?.full_name ?? 'the delegate'
    // Asked in-page (was the browser's confirm).
    if (!(await dlg.confirm({
      title: 'Revoke delegation',
      message: `Revoke this delegation? It takes effect immediately — ${delegateName} loses stand-in access right away.`,
      confirmLabel: 'Revoke',
    }))) return
    revoke.mutate(row.id)
  }

  const handleSubmit = () => {
    setFormError('')
    if (mode.kind === 'create') {
      if (!form.delegatorId || !form.delegateId) {
        setFormError('Delegator and delegate are both required.')
        return
      }
      if (form.delegatorId === form.delegateId) {
        setFormError('A delegator cannot delegate to themself.')
        return
      }
    }
    if (!form.startDate || !form.endDate) {
      setFormError('Start date and end date are both required.')
      return
    }
    if (form.endDate < form.startDate) {
      setFormError('End date must not be before start date.')
      return
    }
    const note = form.note.trim() || null
    if (mode.kind === 'create') {
      create.mutate({
        delegator_user_id: form.delegatorId,
        delegate_user_id: form.delegateId,
        start_date: form.startDate,
        end_date: form.endDate,
        note,
      })
    } else if (mode.kind === 'edit') {
      update.mutate({ id: mode.row.id, body: { start_date: form.startDate, end_date: form.endDate, note } })
    }
  }

  if (delegationsQ.isLoading || usersQ.isLoading) return <LoadingBlock />
  if (delegationsQ.isError || usersQ.isError) {
    return <ErrorBlock message="Failed to load approval delegations. Please refresh and try again." />
  }

  const rows = sorted.rows
  const isSubmitting = create.isPending || update.isPending

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-fg-muted">{rows.length} delegation{rows.length === 1 ? '' : 's'}</span>
        {mode.kind === 'closed' && <Button size="sm" onClick={openCreate}>Add delegation</Button>}
      </div>

      {mode.kind !== 'closed' && (
        <DelegationForm
          mode={mode}
          form={form}
          setForm={setForm}
          users={activeUsers}
          error={formError}
          isSubmitting={isSubmitting}
          onSubmit={handleSubmit}
          onCancel={closeForm}
        />
      )}

      <Card className="overflow-x-auto">
        <Table density="compact" className="min-w-max">
          <THead>
            <Tr>
              <SortTh label="Delegator" sortKey="delegator" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="Delegate" sortKey="delegate" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="From" sortKey="start_date" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="To" sortKey="end_date" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="Note" sortKey="note" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <SortTh label="Status" sortKey="status" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
              <Th />
            </Tr>
          </THead>
          <TBody>
            {rows.length === 0 ? (
              <Tr>
                <Td align="center" colSpan={7} className="text-sm text-fg-muted">
                  No delegations yet.
                </Td>
              </Tr>
            ) : (
              rows.map((row, i) => {
                const status = deriveStatus(row, today)
                const delegatorName = delegatorNameOf(row) ?? '—'
                const delegateName = delegateNameOf(row) ?? '—'
                const isRevoked = status === 'Revoked'
                return (
                  <Tr
                    key={row.id}
                    className={cn('border-b border-line-subtle', i === rows.length - 1 && 'border-b-0')}
                  >
                    <Td className="text-fg-secondary">{delegatorName}</Td>
                    <Td className="text-fg-secondary">{delegateName}</Td>
                    <Td className="text-fg-muted">{formatDate(row.start_date)}</Td>
                    <Td className="text-fg-muted">{formatDate(row.end_date)}</Td>
                    <Td className="max-w-xs truncate text-fg-muted">{row.note || '—'}</Td>
                    <Td><Badge variant={STATUS_VARIANT[status]}>{status}</Badge></Td>
                    <Td>
                      {!isRevoked && (
                        <div className="flex items-center gap-1">
                          <Button variant="ghost" size="sm" onClick={() => openEdit(row)}>Edit</Button>
                          <Button
                            variant="ghost" size="sm"
                            className="text-danger-fg hover:bg-danger-soft"
                            onClick={() => handleRevoke(row)}
                            disabled={revoke.isPending}
                          >
                            Revoke
                          </Button>
                        </div>
                      )}
                    </Td>
                  </Tr>
                )
              })
            )}
          </TBody>
        </Table>
      </Card>
      {dlg.dialog}
    </div>
  )
}

// ── Create / edit form ──────────────────────────────────────────────────────

interface DelegationFormProps {
  mode: Extract<FormMode, { kind: 'create' } | { kind: 'edit' }>
  form: FormState
  setForm: Dispatch<SetStateAction<FormState>>
  users: ApiUser[]
  error: string
  isSubmitting: boolean
  onSubmit: () => void
  onCancel: () => void
}

function DelegationForm({ mode, form, setForm, users, error, isSubmitting, onSubmit, onCancel }: DelegationFormProps) {
  const isEdit = mode.kind === 'edit'

  return (
    <Card className="flex flex-col gap-4 p-4">
      <h2 className="text-sm font-semibold text-fg-secondary">{isEdit ? 'Edit delegation' : 'New delegation'}</h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField label="Delegator" required>
          {isEdit ? (
            <div className="flex h-10 items-center rounded-lg border border-line bg-surface-subtle px-3 text-sm text-fg-muted">
              {mode.row.delegator_name ?? '—'}
            </div>
          ) : (
            <UserCombobox
              users={users}
              value={form.delegatorId}
              onChange={(id) => setForm((f) => ({ ...f, delegatorId: id, delegateId: f.delegateId === id ? '' : f.delegateId }))}
              placeholder="Select delegator…"
            />
          )}
        </FormField>

        <FormField label="Delegate" required hint="Stands in for the delegator during this window.">
          {isEdit ? (
            <div className="flex h-10 items-center rounded-lg border border-line bg-surface-subtle px-3 text-sm text-fg-muted">
              {mode.row.delegate_name ?? '—'}
            </div>
          ) : (
            <UserCombobox
              users={users.filter((u) => u.id !== form.delegatorId)}
              value={form.delegateId}
              onChange={(id) => setForm((f) => ({ ...f, delegateId: id }))}
              placeholder="Select delegate…"
            />
          )}
        </FormField>

        <FormField label="Start date" required>
          <Input
            type="date" value={form.startDate}
            onChange={(e) => setForm((f) => ({ ...f, startDate: e.target.value }))}
          />
        </FormField>

        <FormField label="End date" required>
          <Input
            type="date" value={form.endDate}
            onChange={(e) => setForm((f) => ({ ...f, endDate: e.target.value }))}
          />
        </FormField>
      </div>

      {/* Rendered right next to the dates — a 409 overlap or 422 date-order
          error is fixed by changing one of the two fields above, not by
          dismissing a toast. */}
      {error && <ErrorBlock message={error} />}

      <FormField label="Note" hint="Optional — e.g. the reason for the delegation.">
        <Input
          value={form.note} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
          placeholder="Optional note"
        />
      </FormField>

      <div className="flex items-center gap-2">
        <Button onClick={onSubmit} disabled={isSubmitting}>
          {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {isEdit ? 'Save changes' : 'Create delegation'}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>Cancel</Button>
      </div>
    </Card>
  )
}
