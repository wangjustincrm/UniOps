import { useDeferredValue, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Search, UserPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAssignMatch } from '@/hooks/useInvoices'
import { userService } from '@/services/users'

import { Input, MenuItem, Modal } from '@uniops/shell'
export function AssignMatchDialog({ invoiceId, currentAssigneeName, courier = false, onClose, onAssigned }: {
  invoiceId: string
  currentAssigneeName?: string | null
  /** A courier invoice: the work is matching its shipments, not a PO. */
  courier?: boolean
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
    queryFn: () => userService.directory({ search: search || undefined }),
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

  return (
    <Modal
      open
      onClose={onClose}
      busy={assign.isPending}
      // Closed only by its buttons, as before.
      closeOnEscape={false}
      size="md"
      icon={<UserPlus />}
      title={`${currentAssigneeName ? 'Reassign' : 'Assign'} ${courier ? 'Shipment' : 'PO'} Matching`}
      bodyClassName="flex flex-col gap-3"
      footer={<>
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={!selected || assign.isPending} onClick={submit}>
            {assign.isPending ? 'Assigning…' : 'Assign & Notify'}
          </Button>
      </>}
    >
          {currentAssigneeName && (
            <p className="text-xs text-fg-muted">
              Currently assigned to <span className="font-medium">{currentAssigneeName}</span> — picking a new
              person reassigns the task and notifies them.
            </p>
          )}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted" />
            <Input inputSize="sm" autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people by name..."
              className="w-full pl-8 pr-3" />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto rounded-lg border border-line divide-y divide-line-subtle">
            {isLoading ? (
              <div className="flex justify-center py-6 text-fg-faint"><Loader2 className="h-5 w-5 animate-spin" /></div>
            ) : isError ? (
              <p className="py-6 text-center text-xs text-danger-fg">Failed to load users — please retry</p>
            ) : users.length === 0 ? (
              <p className="py-6 text-center text-xs text-fg-muted">No users found</p>
            ) : users.map((u) => (
              <MenuItem key={u.id} active={selected?.id === u.id}
                onClick={() => setSelected({ id: u.id, name: u.full_name })}
                className={selected?.id === u.id ? 'font-medium' : undefined}>
                {u.full_name}
                {u.department_name && <span className="ml-2 text-xs text-fg-muted">{u.department_name}</span>}
              </MenuItem>
            ))}
          </div>
          {assign.isError && (
            <p className="text-xs text-danger-fg">
              {assign.error instanceof Error ? assign.error.message : 'Assign failed'}
            </p>
          )}
    </Modal>
  )
}
