import { api, fetchAllPages } from '@/lib/api'

export interface ApiPart {
  id: string
  code: string
  category: string
  name: string
  description?: string
  supplier: string
  supplier_part_no: string
  supplier_item_id?: string
  unit_price: number
  unit: string
  is_active: boolean
  created_at: string
  image_url?: string | null
}

export interface CreatePartBody {
  code: string
  category: string
  name: string
  description?: string
  supplier: string
  supplier_part_no: string
  supplier_item_id?: string
  unit_price: number
  unit: string
  is_active?: boolean
}

export interface UpdatePartBody {
  code?: string
  category?: string
  name?: string
  description?: string
  supplier?: string
  supplier_part_no?: string
  supplier_item_id?: string
  unit_price?: number
  unit?: string
  is_active?: boolean
}

export interface PartFilters {
  search?: string
  category?: string
  supplier?: string
  active_only?: boolean
  page?: number
  page_size?: number
}

export interface PartListResponse {
  items: ApiPart[]
  total: number
}

export const partService = {
  list: (filters?: PartFilters) =>
    api.get<PartListResponse>('/parts', filters),

  listAll: (filters?: Omit<PartFilters, 'page' | 'page_size'>): Promise<PartListResponse> =>
    fetchAllPages((page, page_size) => partService.list({ ...filters, page, page_size })),

  categories: () =>
    api.get<string[]>('/parts/categories'),

  get: (id: string) =>
    api.get<ApiPart>(`/parts/${id}`),

  create: (body: CreatePartBody) =>
    api.post<ApiPart>('/parts', body),

  update: (id: string, body: UpdatePartBody) =>
    api.patch<ApiPart>(`/parts/${id}`, body),

  delete: (id: string) =>
    api.delete<void>(`/parts/${id}`),

  /** Download the parts catalog as CSV — returns a Blob */
  exportCsv: async (): Promise<void> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'

    const response = await fetch(`${base}/parts/export`, {
      method: 'GET',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })

    if (!response.ok) {
      throw new Error(`Parts export failed: ${response.status} ${response.statusText}`)
    }

    const blob = await response.blob()
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = 'parts-catalog.csv'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(objectUrl)
  },

  /** Import parts from a CSV file (multipart/form-data) */
  importCsv: async (file: File): Promise<{ imported: number; errors: number }> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const formData = new FormData()
    formData.append('file', file)

    const response = await fetch(`${base}/parts/import`, {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    })

    if (!response.ok) {
      throw new Error(`Parts import failed: ${response.status} ${response.statusText}`)
    }

    return response.json()
  },
}
