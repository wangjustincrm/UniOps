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

export const departmentService = {
  list: () =>
    api.get<DepartmentListResponse>('/departments'),

  get: (id: string) =>
    api.get<ApiDepartment>(`/departments/${id}`),

  create: (body: CreateDepartmentBody) =>
    api.post<ApiDepartment>('/departments', body),

  update: (id: string, body: UpdateDepartmentBody) =>
    api.patch<ApiDepartment>(`/departments/${id}`, body),

  delete: (id: string) =>
    api.delete<void>(`/departments/${id}`),
}
