import { api, fetchAllPages } from '@/lib/api'

export interface ApiVendor {
  id: string
  code: string
  erp_id?: string
  name: string
  category: string
  contact_name: string
  contact_email: string
  // Where remittance advice is emailed. Falls back to contact_email when
  // blank — see finance-api's vendor resolution (Task 2). May be null on
  // legacy responses.
  remittance_email?: string | null
  phone?: string
  address?: string
  payment_terms: 'net15' | 'net30' | 'net60' | 'net90' | 'cod' | 'prepayment'
  max_prepayment_pct: number | null
  currency: string
  notes?: string
  is_active: boolean
  created_at: string
}

export interface CreateVendorBody {
  code: string
  erp_id?: string
  name: string
  category: string
  contact_name: string
  contact_email: string
  remittance_email?: string
  phone?: string
  address?: string
  payment_terms: 'net15' | 'net30' | 'net60' | 'net90' | 'cod' | 'prepayment'
  max_prepayment_pct?: number | null
  currency: string
  notes?: string
  is_active?: boolean
}

export interface UpdateVendorBody {
  code?: string
  erp_id?: string
  name?: string
  category?: string
  contact_name?: string
  contact_email?: string
  remittance_email?: string
  phone?: string
  address?: string
  payment_terms?: 'net15' | 'net30' | 'net60' | 'net90' | 'cod' | 'prepayment'
  max_prepayment_pct?: number | null
  currency?: string
  notes?: string
  is_active?: boolean
}

export interface VendorFilters {
  search?: string
  category?: string
  active_only?: boolean
  page?: number
  page_size?: number
}

export interface VendorListResponse {
  items: ApiVendor[]
  total: number
}

export interface VendorImportResult {
  created: number
  updated: number
  errors: string[]
}

export const vendorService = {
  list: (filters?: VendorFilters) =>
    api.get<VendorListResponse>('/vendors', filters),

  listAll: (filters?: Omit<VendorFilters, 'page' | 'page_size'>): Promise<VendorListResponse> =>
    fetchAllPages((page, page_size) => vendorService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiVendor>(`/vendors/${id}`),

  create: (body: CreateVendorBody) =>
    api.post<ApiVendor>('/vendors', body),

  update: (id: string, body: UpdateVendorBody) =>
    api.patch<ApiVendor>(`/vendors/${id}`, body),

  importCsv: async (file: File): Promise<VendorImportResult> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const formData = new FormData()
    formData.append('file', file)

    const response = await fetch(`${base}/vendors/import`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    })

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try {
        const body = await response.json()
        if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch {}
      throw new Error(`Vendor import failed: ${detail}`)
    }

    return response.json()
  },
}
