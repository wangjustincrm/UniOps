import { api } from '@/lib/api'

export interface ApiDepartment {
  id: string
  name: string
  code: string
  is_active: boolean
}

export interface CreateDepartmentBody {
  name: string
  code: string
  is_active?: boolean
}

export interface UpdateDepartmentBody {
  name?: string
  code?: string
  is_active?: boolean
}

export interface DepartmentListResponse {
  items: ApiDepartment[]
  total: number
}

// NOTE: The EPMS /departments endpoints are GET-only — create/update/delete were
// moved to mdm-api. Do not re-add write methods here; call mdm-api instead.
export const departmentService = {
  list: () =>
    api.get<DepartmentListResponse>('/departments'),

  get: (id: string) =>
    api.get<ApiDepartment>(`/departments/${id}`),
}
