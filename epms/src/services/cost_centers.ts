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

// NOTE: The EPMS /cost-centers endpoints are GET-only — create/update/delete were
// moved to mdm-api. Do not re-add write methods here; call mdm-api instead.
export const costCenterService = {
  list: (filters?: CostCenterFilters) =>
    api.get<ApiCostCenter[]>('/cost-centers', filters),

  get: (id: string) =>
    api.get<ApiCostCenter>(`/cost-centers/${id}`),
}
