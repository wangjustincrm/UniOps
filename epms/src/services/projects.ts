import { api } from '@/lib/api'

export type ProjectStatus = 'active' | 'on_hold' | 'closed'

export interface ApiProject {
  id: string
  code: string
  name: string
  description?: string | null
  status: ProjectStatus
  budget: number
  currency: string
  start_date?: string | null
  end_date?: string | null
  owner_dept?: string | null
  manager_name?: string | null
}

export interface CreateProjectBody {
  code: string
  name: string
  description?: string
  status?: ProjectStatus
  budget?: number
  currency?: string
  start_date?: string
  end_date?: string
  owner_dept?: string
  manager_name?: string
}

export interface UpdateProjectBody {
  name?: string
  description?: string
  status?: ProjectStatus
  budget?: number
  currency?: string
  start_date?: string
  end_date?: string
  owner_dept?: string
  manager_name?: string
}

export interface ProjectFilters {
  status?: string
  search?: string
}

export const projectService = {
  list: (filters?: ProjectFilters) =>
    api.get<ApiProject[]>('/projects', filters),

  get: (id: string) =>
    api.get<ApiProject>(`/projects/${id}`),

  create: (body: CreateProjectBody) =>
    api.post<ApiProject>('/projects', body),

  update: (id: string, body: UpdateProjectBody) =>
    api.patch<ApiProject>(`/projects/${id}`, body),
}
