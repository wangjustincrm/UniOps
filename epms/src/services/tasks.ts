import { api } from '@/lib/api'

export type TaskType =
  | 'approve_pr'
  | 'approve_po'
  | 'approve_pa'
  | 'process_pa'
  | 'revise_pr'
  | 'revise_pa'
  | 'create_po'
  | 'place_order'
  | 'revise_po'
  | 'acknowledge_gr'
  | 'gr_damage_report'
  | 'collect_goods'
  | 'confirm_service_gr'
  | 'confirm_settlement'
  | 'review_match'
  | 'match_invoice'
  | 'create_pa'
  | 'create_prepayment_pa'
  | 'approve_budget_plan'
  | 'revise_budget_plan'
  | 'approve_agr'
  | 'revise_agr'

export interface ApiTask {
  id: string
  type: TaskType
  priority: 'urgent' | 'normal'
  document_type: string
  document_id: string
  document_number: string
  assigned_role: string
  title: string
  description?: string
  due_date?: string
  amount?: number
  vendor?: string
  is_completed: boolean
  created_at: string
  completed_at?: string
}

export interface TaskFilters {
  type?: TaskType
  priority?: 'urgent' | 'normal'
  is_completed?: boolean
  document_type?: string
}

export interface TaskListResponse {
  items: ApiTask[]
  total: number
}

export const taskService = {
  list: (filters?: TaskFilters) =>
    api.get<TaskListResponse>('/tasks', filters),

  complete: (id: string) =>
    api.post<ApiTask>(`/tasks/${id}/complete`),
}
