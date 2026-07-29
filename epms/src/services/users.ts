import { api, fetchAllPages } from '@/lib/api'

export type ApiUserRole =
  | 'requester'
  | 'dept_manager'
  | 'gm'
  | 'opm'
  | 'procurement_officer'
  | 'procurement_manager'
  | 'warehouse_staff'
  | 'ap_clerk'
  | 'finance_manager'
  | 'finance_bp'
  | 'cfo'
  | 'auditor'
  | 'vendor_manager'
  | 'system_admin'

export interface ApiUser {
  id: string
  email: string
  full_name: string
  role: ApiUserRole
  erp_person_code?: string | null
  department_id: string | null
  department_name: string | null
  is_active: boolean
  mfa_enabled: boolean
  teams_account: string | null
  supervisor_id?: string | null
}

export interface CreateUserBody {
  email: string
  full_name: string
  role: ApiUserRole
  erp_person_code: string
  department_id?: string | null
  is_active?: boolean
  password: string
  teams_account?: string | null
}

export interface UpdateUserBody {
  email?: string
  full_name?: string
  role?: ApiUserRole
  erp_person_code?: string
  department_id?: string | null
  is_active?: boolean
  password?: string
  teams_account?: string | null
  supervisor_id?: string | null
}

export interface UserFilters {
  role?: ApiUserRole
  department_id?: string
  is_active?: boolean
  search?: string
  page?: number
  page_size?: number
}

export interface UserListResponse {
  items: ApiUser[]
  total: number
}

// Public directory (GET /users/directory) — open to any authenticated user,
// active users only, non-sensitive fields. Use this for people pickers;
// GET /users is system_admin-only and 403s for everyone else.
export interface ApiUserBrief {
  id: string
  full_name: string
  email: string
  role: string
  department_id: string | null
  department_name: string | null
}

export interface UserBriefListResponse {
  items: ApiUserBrief[]
  total: number
}

export const userService = {
  list: (filters?: UserFilters) =>
    api.get<UserListResponse>('/users', filters),

  listAll: (filters?: Omit<UserFilters, 'page' | 'page_size'>): Promise<UserListResponse> =>
    fetchAllPages((page, page_size) => userService.list({ ...filters, page, page_size })),

  directory: (opts?: { search?: string; role?: string; department_id?: string; department_ids?: string[] }) =>
    api.get<UserBriefListResponse>('/users/directory', {
      search: opts?.search || undefined,
      role: opts?.role || undefined,
      // department_ids (repeatable) scopes across MULTIPLE departments — used by
      // the PR list Requester picker for a Director/GM covering several depts.
      department_ids: opts?.department_ids && opts.department_ids.length ? opts.department_ids : undefined,
      department_id: opts?.department_id || undefined,
      page_size: 100,
    }),

  get: (id: string) =>
    api.get<ApiUser>(`/users/${id}`),

  create: (body: CreateUserBody) =>
    api.post<ApiUser>('/users', body),

  update: (id: string, body: UpdateUserBody) =>
    api.patch<ApiUser>(`/users/${id}`, body),

  delete: (id: string) =>
    api.delete<void>(`/users/${id}`),
}
