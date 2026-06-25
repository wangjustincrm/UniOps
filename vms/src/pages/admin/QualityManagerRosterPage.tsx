/** Quality Manager roster editor (W11 UI; W10 backend).
 *
 * Local list of UniOps user UUIDs. Search the directory, pick users, save
 * the whole list atomically. First-active strategy in vms-api picks
 * roster[0] (active) when a GMP visit needs QM approval — order matters.
 */
import { useState } from 'react'
import { CheckCircle2, Loader2, Search, ShieldCheck, X } from 'lucide-react'
import {
  useQualityManagerRoster, useSetQualityManagerRoster, useUserDirectory, useUserBrief,
  type UserBrief,
} from '@/services/api'

export default function QualityManagerRosterPage() {
  const { data, isLoading } = useQualityManagerRoster()
  const save = useSetQualityManagerRoster()

  // `null` = no local edits yet (display server data). Once the user
  // adds/removes/reorders, the override owns the displayed value until
  // Save succeeds (resets to null).
  const [draftOverride, setDraftOverride] = useState<string[] | null>(null)
  const draft = draftOverride ?? data?.user_ids ?? []
  const setDraft = (next: string[]) => setDraftOverride(next)

  const [query, setQuery] = useState('')
  const dir = useUserDirectory(query)

  const addId = (id: string) => {
    if (!draft.includes(id)) setDraft([...draft, id])
    setQuery('')
  }
  const removeId = (id: string) => setDraft(draft.filter(x => x !== id))

  const move = (id: string, direction: -1 | 1) => {
    const i = draft.indexOf(id)
    if (i < 0) return
    const j = i + direction
    if (j < 0 || j >= draft.length) return
    const next = [...draft]
    ;[next[i], next[j]] = [next[j], next[i]]
    setDraft(next)
  }

  const onSave = () =>
    save.mutate({ user_ids: draft }, { onSuccess: () => setDraftOverride(null) })

  const dirty = draftOverride !== null

  return (
    <div className="max-w-3xl">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-neutral-900">Quality Manager roster</h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            For GMP / Lab visits, vms-api picks the first <span className="font-medium">active</span>{' '}
            user from this list as the Quality Manager step approver. Order matters.
          </p>
        </div>
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          {save.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <CheckCircle2 className="h-4 w-4" />}
          Save
        </button>
      </div>

      {save.error && (
        <p className="mt-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p className="mt-2 rounded-md bg-success-50 px-3 py-2 text-xs text-success-600">
          Roster saved.
        </p>
      )}

      {/* Current roster (ordered) */}
      <div className="mt-5">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Current ({draft.length})
        </h3>
        <div className="rounded-md border border-neutral-200 bg-white">
          {isLoading && (
            <p className="px-3 py-4 text-xs text-neutral-400">Loading…</p>
          )}
          {!isLoading && draft.length === 0 && (
            <p className="px-3 py-4 text-xs text-neutral-400">
              No Quality Managers configured. GMP / Lab visits will have no second-step approver.
            </p>
          )}
          {draft.map((id, idx) => (
            <RosterRow
              key={id}
              userId={id}
              order={idx + 1}
              canMoveUp={idx > 0}
              canMoveDown={idx < draft.length - 1}
              onMoveUp={() => move(id, -1)}
              onMoveDown={() => move(id, 1)}
              onRemove={() => removeId(id)}
            />
          ))}
        </div>
      </div>

      {/* Add by search */}
      <div className="mt-6">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Add user
        </h3>
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-neutral-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or email (≥ 2 chars)…"
            className="w-full rounded-md border border-neutral-300 bg-white py-2 pl-8 pr-3 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          />
        </div>
        {query.length >= 2 && (
          <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-neutral-200 bg-white">
            {dir.isLoading && <p className="px-3 py-2 text-xs text-neutral-500">Searching…</p>}
            {!dir.isLoading && (dir.data?.items.length ?? 0) === 0 && (
              <p className="px-3 py-2 text-xs text-neutral-500">No matches.</p>
            )}
            {dir.data?.items.map((u: UserBrief) => {
              const already = draft.includes(u.id)
              return (
                <button
                  key={u.id}
                  type="button"
                  disabled={already}
                  onClick={() => addId(u.id)}
                  className="block w-full border-b border-neutral-100 px-3 py-2 text-left text-sm hover:bg-primary-50/40 disabled:cursor-not-allowed disabled:bg-neutral-50 disabled:text-neutral-400"
                >
                  <p className="font-medium">{u.full_name}</p>
                  <p className="text-xs text-neutral-500">
                    {u.email}
                    {u.department_name && ` · ${u.department_name}`}
                    {already && ' · already in roster'}
                  </p>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Roster row ──────────────────────────────────────────────────────────────-

function RosterRow({
  userId, order, canMoveUp, canMoveDown, onMoveUp, onMoveDown, onRemove,
}: {
  userId: string; order: number
  canMoveUp: boolean; canMoveDown: boolean
  onMoveUp: () => void; onMoveDown: () => void; onRemove: () => void
}) {
  const { data: user, isLoading } = useUserBrief(userId)
  return (
    <div className="flex items-center justify-between gap-2 border-b border-neutral-100 px-3 py-2 last:border-b-0">
      <div className="flex items-center gap-3 min-w-0">
        <ShieldCheck className={
          order === 1 ? 'h-4 w-4 text-success-600' : 'h-4 w-4 text-neutral-300'
        } />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-neutral-800">
            {isLoading ? '…' : user?.full_name ?? <span className="text-neutral-400">Unknown user</span>}
            {order === 1 && (
              <span className="ml-2 inline-flex items-center rounded-full bg-success-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-success-600">
                First active
              </span>
            )}
          </p>
          <p className="truncate text-xs text-neutral-500">
            {isLoading ? '' : user?.email ?? userId}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <button
          onClick={onMoveUp}
          disabled={!canMoveUp}
          className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 disabled:opacity-30 disabled:cursor-not-allowed"
          aria-label="Move up"
          title="Move up"
        >▲</button>
        <button
          onClick={onMoveDown}
          disabled={!canMoveDown}
          className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 disabled:opacity-30 disabled:cursor-not-allowed"
          aria-label="Move down"
          title="Move down"
        >▼</button>
        <button
          onClick={onRemove}
          className="rounded p-1 text-danger-600 hover:bg-danger-50"
          aria-label="Remove"
          title="Remove"
        ><X className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  )
}
