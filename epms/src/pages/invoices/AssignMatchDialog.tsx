import { useDeferredValue, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Search, UserPlus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAssignMatch } from '@/hooks/useInvoices'
import { userService } from '@/services/users'

export function AssignMatchDialog({ invoiceId, currentAssigneeName, onClose, onAssigned }: {
  invoiceId: string
  currentAssigneeName?: string | null
  onClose: () => void
  onAssigned?: () => void
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null)
  const assign = useAssignMatch()

  // /users/directory is the picker endpoint (any authenticated user, active
  // only, server-side search) — GET /users is system_admin-only and 403s here.
  const search = useDeferredValue(query.trim())
  const { data, isLoading, isError } = useQuery({
    queryKey: ['users', 'directory', search],
    queryFn: () => userService.directory(search || undefined),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  })
  const users = data?.items ?? []

  const submit = () => {
    if (!selected) return
    assign.mutate({ id: invoiceId, userId: selected.id }, {
      onSuccess: () => { onAssigned?.(); onClose() },
    })
  }

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <UserPlus className="h-4 w-4 text-primary-600" />
            <h2 className="text-sm font-semibold text-neutral-900">
              {currentAssigneeName ? 'Reassign PO Matching' : 'Assign PO Matching'}
            </h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-3 px-5 py-4 min-h-0">
          {currentAssigneeName && (
            <p className="text-xs text-neutral-500">
              Currently assigned to <span className="font-medium">{currentAssigneeName}</span> — picking a new
              person reassigns the task and notifies them.
            </p>
          )}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people by name..."
              className="w-full h-9 pl-8 pr-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto rounded-lg border border-neutral-200 divide-y divide-neutral-100">
            {isLoading ? (
              <div className="flex justify-center py-6 text-neutral-300"><Loader2 className="h-5 w-5 animate-spin" /></div>
            ) : isError ? (
              <p className="py-6 text-center text-xs text-danger-600">Failed to load users — please retry</p>
            ) : users.length === 0 ? (
              <p className="py-6 text-center text-xs text-neutral-400">No users found</p>
            ) : users.map((u) => (
              <button key={u.id}
                onClick={() => setSelected({ id: u.id, name: u.full_name })}
                className={`w-full px-3 py-2 text-left text-sm hover:bg-primary-50 ${selected?.id === u.id ? 'bg-primary-50 font-medium' : ''}`}>
                {u.full_name}
                {u.department_name && <span className="ml-2 text-xs text-neutral-400">{u.department_name}</span>}
              </button>
            ))}
          </div>
          {assign.isError && (
            <p className="text-xs text-danger-600">
              {assign.error instanceof Error ? assign.error.message : 'Assign failed'}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3.5">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={!selected || assign.isPending} onClick={submit}>
            {assign.isPending ? 'Assigning…' : 'Assign & Notify'}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  )
}
