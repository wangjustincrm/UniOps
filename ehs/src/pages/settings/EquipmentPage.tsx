/**
 * The equipment register.
 *
 * Kept in Settings because it is reference data HSE maintains, not a record
 * anyone files. The spreadsheet upload is the primary way it gets populated —
 * four hundred machines are not typed in one at a time — so it is the first
 * thing on the page rather than hidden behind a menu.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { PageHeader } from '@/components/ui'

interface Equipment {
  id: string; asset_no: string | null; name: string; equipment_type: string | null
  location_path: string | null; department_name: string | null; is_active: boolean
}
interface ImportResult { created: number; updated: number; skipped: number; problems: string[] }

export default function EquipmentPage() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const list = useQuery({
    queryKey: ['equipment', search],
    queryFn: () => api.get<Equipment[]>(
      `/api/v1/equipment${search ? `?search=${encodeURIComponent(search)}` : ''}`),
  })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file, file.name)
      return api.upload<ImportResult>('/api/v1/equipment/import', form)
    },
    onSuccess: (r) => {
      setResult(r)
      queryClient.invalidateQueries({ queryKey: ['equipment'] })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="p-4">
      <PageHeader title="Equipment" />
      <p className="mb-4 text-sm text-neutral-600">
        Picking a machine on a form fills in its area and department from here.
      </p>

      <div className="mb-4 rounded-xl border border-dashed border-neutral-300 bg-neutral-50 p-4">
        <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden"
               onChange={(e) => {
                 const f = e.target.files?.[0]
                 e.target.value = ''
                 if (f) { setError(null); setResult(null); upload.mutate(f) }
               }} />
        <button type="button" disabled={upload.isPending}
                onClick={() => fileInput.current?.click()}
                className="flex min-h-[40px] items-center gap-2 rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50">
          <Upload className="h-4 w-4" aria-hidden />
          {upload.isPending ? 'Loading…' : 'Upload a spreadsheet'}
        </button>
        <p className="mt-2 text-xs text-neutral-500">
          Save your sheet as CSV. Columns matched: asset no, name, type, area, department,
          manufacturer, model, serial. Rows are matched on asset number, so re-uploading a
          corrected sheet updates rather than duplicates.
        </p>
        {result && (
          <div className="mt-2 rounded-lg bg-white p-2.5 text-xs">
            <p className="font-medium text-neutral-800">
              {result.created} added · {result.updated} updated
              {result.skipped > 0 && ` · ${result.skipped} skipped`}
            </p>
            {result.problems.map((p, i) => (
              <p key={i} className="mt-1 text-warning-700">{p}</p>
            ))}
          </div>
        )}
        {error && <p className="mt-2 text-xs text-danger-700">{error}</p>}
      </div>

      <div className="relative mb-3">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                aria-hidden />
        <input value={search} onChange={(e) => setSearch(e.target.value)}
               placeholder="Search by name or asset number"
               className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white pl-8 pr-3 text-sm focus:border-primary-400 focus:outline-none" />
      </div>

      {list.data?.length === 0 && (
        <p className="p-8 text-center text-sm text-neutral-500">
          {search ? `Nothing matches “${search}”.` : 'No equipment yet — upload a spreadsheet.'}
        </p>
      )}
      <ul className="flex flex-col gap-1.5">
        {list.data?.map((e) => (
          <li key={e.id} className="rounded-lg border border-neutral-200 bg-white p-3">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-xs text-neutral-500">{e.asset_no ?? '—'}</span>
              <span className="min-w-0 flex-1 text-sm font-medium text-neutral-900">{e.name}</span>
              {e.equipment_type && (
                <span className="text-xs text-neutral-500">{e.equipment_type}</span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-neutral-500">
              {e.location_path ? e.location_path.split('/').slice(1).join(' › ') : 'No area'}
              {e.department_name ? ` · ${e.department_name}` : ''}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}
