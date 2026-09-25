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
import { Plus, Search, Upload } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAreaPath } from '@/lib/areaPath'
import {
  Card, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import { LocationSelect } from '@/components/LocationSelect'

interface Equipment {
  id: string; asset_no: string | null; name: string; equipment_type: string | null
  location_id: string | null
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

  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Equipment | null>(null)

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
      <PageHeader title="Equipment">
        {/* ★ One machine at a time, as well as a spreadsheet. The endpoint was
            always there; this screen only ever offered the upload, so adding
            the one press that arrived this morning meant editing a CSV and
            re-importing the lot. */}
        <button type="button" className={primaryButton}
                onClick={() => { setAdding(true); setEditing(null) }}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a machine
        </button>
      </PageHeader>
      <p className="mb-4 text-sm text-neutral-600">
        Picking a machine on a form fills in its area and department from here.
      </p>

      {(adding || editing) && (
        <EquipmentForm
          equipment={editing}
          onClose={() => { setAdding(false); setEditing(null) }}
          onSaved={() => {
            setAdding(false)
            setEditing(null)
            queryClient.invalidateQueries({ queryKey: ['equipment'] })
          }}
        />
      )}

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
          {search
            ? `Nothing matches “${search}”.`
            : 'No equipment yet — add one, or upload a spreadsheet.'}
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
            <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
              <p className="min-w-0 flex-1 text-xs text-neutral-500">
                {formatAreaPath(e.location_path) ?? 'No area'}
                {e.department_name ? ` · ${e.department_name}` : ''}
              </p>
              <button type="button" onClick={() => { setEditing(e); setAdding(false) }}
                      className="min-h-[28px] text-xs font-medium text-primary-700 hover:underline">
                Edit
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}


/**
 * One machine, added or corrected by hand.
 *
 * The area is what earns its place here: choosing it fills in the department
 * on every form that names this machine, which is the whole reason this
 * register exists. The server derives the department from the area rather than
 * asking twice — two answers to one question is how they come to disagree.
 */
function EquipmentForm(
  { equipment, onClose, onSaved }:
  { equipment: Equipment | null; onClose: () => void; onSaved: () => void },
) {
  const [assetNo, setAssetNo] = useState(equipment?.asset_no ?? '')
  const [name, setName] = useState(equipment?.name ?? '')
  const [type, setType] = useState(equipment?.equipment_type ?? '')
  const [locationId, setLocationId] = useState<string | null>(equipment?.location_id ?? null)
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => {
      const body = {
        asset_no: assetNo.trim() || null,
        name: name.trim(),
        equipment_type: type.trim() || null,
        location_id: locationId,
      }
      return equipment
        ? api.patch(`/api/v1/equipment/${equipment.id}`, body)
        : api.post('/api/v1/equipment', body)
    },
    onSuccess: onSaved,
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <Card className="mb-4">
      <SectionTitle className="mb-2">
        {equipment ? `Edit ${equipment.name}` : 'Add a machine'}
      </SectionTitle>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Line 1 DSI" />
        </Field>
        <Field label="Asset number" hint="Optional, but it is what a spreadsheet matches on">
          <input className={inputClass} value={assetNo}
                 onChange={(e) => setAssetNo(e.target.value)} />
        </Field>
        <Field label="Kind">
          <input className={inputClass} value={type} onChange={(e) => setType(e.target.value)}
                 placeholder="Direct steam injection" />
        </Field>
        <Field label="Where it is"
               hint="The department comes from this — picking the machine on a form fills both in">
          <LocationSelect value={locationId} onChange={setLocationId}
                          placeholder="Pick an area" />
        </Field>
      </div>
      {error && <ErrorNote error={new Error(error)} />}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!name.trim() || save.isPending}
                onClick={() => { if (name.trim() && !save.isPending) { setError(null); save.mutate() } }}>
          {save.isPending ? 'Saving…' : equipment ? 'Save' : 'Add it'}
        </button>
      </div>
    </Card>
  )
}
