import { api } from '@/lib/api'

export interface ApiCostCenter {
  id: string
  code: string
  name: string
  department_id: string
  is_active: boolean
  created_at: string
}

export interface CreateCostCenterBody {
  code: string
  name: string
  department_id: string
  is_active?: boolean
}

export interface UpdateCostCenterBody {
  code?: string
  name?: string
  department_id?: string
  is_active?: boolean
}

export interface CostCenterFilters {
  department_id?: string
  active_only?: boolean
}

export const costCenterService = {
  list: (filters?: CostCenterFilters) =>
    api.get<ApiCostCenter[]>('/cost-centers', filters),

  get: (id: string) =>
    api.get<ApiCostCenter>(`/cost-centers/${id}`),

  create: (body: CreateCostCenterBody) =>
    api.post<ApiCostCenter>('/cost-centers', body),

  update: (id: string, body: UpdateCostCenterBody) =>
    api.patch<ApiCostCenter>(`/cost-centers/${id}`, body),

  delete: (id: string) =>
    api.delete<void>(`/cost-centers/${id}`),
}
