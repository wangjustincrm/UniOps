/**
 * Departments — read-only hook for EPMS pages (PR create, User forms, etc.).
 *
 * Writes (create/update/delete) have moved to Portal → Admin → Departments,
 * which calls mdm-api directly. Use that surface, not this hook.
 */
import { useQuery } from '@tanstack/react-query'
import { departmentService } from '@/services/departments'

export function useDepartments() {
  return useQuery({
    queryKey: ['departments'],
    queryFn: () => departmentService.list(),
  })
}

export function useDepartment(id: string) {
  return useQuery({
    queryKey: ['departments', id],
    queryFn: () => departmentService.get(id),
    enabled: Boolean(id),
  })
}
