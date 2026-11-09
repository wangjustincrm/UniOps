/**
 * The equipment register.
 *
 * Kept in Settings because it is reference data HSE maintains, not a record
 * anyone files. The spreadsheet upload is how most of it arrives, so it sits
 * near the top rather than behind a menu.
 *
 * ★ Round 12, EQ-01: "the equipment registry does not show all the equipment
 * in the list and it should." It showed the first 200 of 7,170 by name and
 * said nothing. Every filter is the server's now, and the pager counts what
 * exists, not what arrived.
 *
 * EQ-01/05/06: a box per System across the top, counting its machines; a box
 * shows only that System. EQ-03: the System is an entry of the list under
 * Settings → Lists, and a System can be merged into another here — the way
 * HSE turn today's 148 values into the groups they want (Q1).
 *
 * Q3: retire hides a machine from every picker and keeps it on the records
 * that name it; delete is offered, and refused by the server, unless nothing
 * names it.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Plus, Upload } from 'lucide-react'
import { api, ApiError, openFile } from '@/lib/api'
import { formatAreaPath } from '@/lib/areaPath'
import {
  Card, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import { LocationSelect } from '@/components/LocationSelect'
import { VocabularySelect } from '@/components/VocabularyPicker'
import { PAGE_SIZE, Pagination, SearchBox } from '@/components/ListView'

interface Equipment {
  id: string; asset_no: string | null; name: string
  description: string | null
  system_id: string | null; system_label: string | null
  manufacturer: string | null; model: string | null; serial_no: string | null
  location_id: string | null; location_path: string | null
  department_id: string | null; department_name: string | null
  is_active: boolean; retired_at: string | null; retired_by_name: string | null
}
interface EquipmentPageData { items: Equipment[]; total: number }
interface SystemCount { system_id: string | null; label: string; count: number; is_active: boolean }
interface Summary { total: number; retired: number; systems: SystemCount[] }
interface ImportResult {
  created: number; updated: number; skipped: number; problems: string[]; new_systems: string[]
}

/** Which machines the list shows: every System, one, those with none, or the retired. */
type Filter = { kind: 'all' } | { kind: 'system'; id: string; label: string }
  | { kind: 'none' } | { kind: 'retired' }

const BOXES = 11

export default function EquipmentPage() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>({ kind: 'all' })
  const [page, setPage] = useState(0)
  const [allBoxes, setAllBoxes] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Equipment | null>(null)

  const choose = (f: Filter) => { setFilter(f); setPage(0) }
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['equipment'] })

  const summary = useQuery({
    queryKey: ['equipment', 'summary'],
    queryFn: () => api.get<Summary>('/api/v1/equipment/summary'),
  })

  const list = useQuery({
    queryKey: ['equipment', 'register', search.trim(), filter, page],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
      if (search.trim()) qs.set('search', search.trim())
      if (filter.kind === 'system') qs.set('system_id', filter.id)
      if (filter.kind === 'none') qs.set('no_system', 'true')
      if (filter.kind === 'retired') qs.set('state', 'retired')
      return api.get<EquipmentPageData>(`/api/v1/equipment?${qs}`)
    },
    placeholderData: (prev) => prev,
  })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file, file.name)
      return api.upload<ImportResult>('/api/v1/equipment/import', form)
    },
    onSuccess: (r) => { setResult(r); refresh() },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const systems = summary.data?.systems ?? []
  const boxes = allBoxes ? systems : systems.slice(0, BOXES)
  const total = list.data?.total ?? 0
  const isOn = (f: Filter) => filter.kind === f.kind
    && (f.kind !== 'system' || (filter.kind === 'system' && filter.id === f.id))

  return (
    <div className="p-4">
      <PageHeader title="Equipment">
        <button type="button" className={primaryButton}
                onClick={() => { setAdding(true); setEditing(null) }}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a machine
        </button>
      </PageHeader>
      <p className="mb-4 text-sm text-neutral-600">
        The machines people pick on incidents, hazards, job analyses and work permits.
        A form offers the machines in the area it is filed against.
      </p>

      {(adding || editing) && (
        <EquipmentForm
          key={editing?.id ?? 'new'}
          equipment={editing}
          onClose={() => { setAdding(false); setEditing(null) }}
          onSaved={() => { setAdding(false); setEditing(null); refresh() }}
        />
      )}

      {/* System boxes — EQ-01/05/06 */}
      <section aria-label="Equipment by System" className="mb-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <SystemBox label="All in service" count={summary.data?.total}
                     on={isOn({ kind: 'all' })} onClick={() => choose({ kind: 'all' })} />
          {boxes.map((s) => {
            const f: Filter = s.system_id
              ? { kind: 'system', id: s.system_id, label: s.label } : { kind: 'none' }
            return (
              <SystemBox key={s.system_id ?? 'none'} label={s.label} count={s.count}
                         muted={!s.is_active} on={isOn(f)} onClick={() => choose(f)} />
            )
          })}
          <SystemBox label="Retired" count={summary.data?.retired} muted
                     on={isOn({ kind: 'retired' })} onClick={() => choose({ kind: 'retired' })} />
        </div>
        {systems.length > BOXES && (
          <button type="button" onClick={() => setAllBoxes(!allBoxes)}
                  className="mt-2 min-h-[32px] text-xs font-medium text-primary-700 hover:underline">
            {allBoxes ? 'Show the largest only' : `Show all ${systems.length} Systems`}
          </button>
        )}
        {summary.isError && <ErrorNote error={summary.error} />}
      </section>

      {filter.kind === 'system' && (
        <MergePanel system={filter} systems={systems}
                    onMerged={(into) => { choose(into); refresh() }} />
      )}

      <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(0) }}
                 placeholder="Search by name, asset number, System or area" />

      {list.isError && <ErrorNote error={list.error} />}
      {list.data && list.data.items.length === 0 && (
        <p className="p-8 text-center text-sm text-neutral-500">
          {search.trim()
            ? `Nothing matches “${search.trim()}”.`
            : filter.kind === 'retired' ? 'No machine has been retired.'
            : 'No equipment here yet — add one, or upload a spreadsheet.'}
        </p>
      )}
      <ul className="flex flex-col gap-1.5">
        {list.data?.items.map((e) => (
          <li key={e.id} className="rounded-lg border border-neutral-200 bg-white p-3">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-xs text-neutral-500">{e.asset_no ?? '—'}</span>
              <span className="min-w-0 flex-1 text-sm font-medium text-neutral-900">{e.name}</span>
              {e.system_label && <span className="text-xs text-neutral-500">{e.system_label}</span>}
              {!e.is_active && (
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                  Retired
                </span>
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
      <Pagination page={page} pageCount={Math.max(1, Math.ceil(total / PAGE_SIZE))}
                  total={total} onPage={setPage} noun="machines" />

      {/* The spreadsheet — EQ-04 */}
      <div className="mt-6 rounded-xl border border-dashed border-neutral-300 bg-neutral-50 p-4">
        <SectionTitle className="mb-2">Load from a spreadsheet</SectionTitle>
        <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden"
               onChange={(e) => {
                 const f = e.target.files?.[0]
                 e.target.value = ''
                 if (f) { setError(null); setResult(null); upload.mutate(f) }
               }} />
        <div className="flex flex-wrap gap-2">
          <button type="button" className={secondaryButton}
                  onClick={() => openFile('/api/v1/equipment/template.csv', 'equipment-template.csv')
                    .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))}>
            <Download className="h-4 w-4" aria-hidden />
            Download the template
          </button>
          <button type="button" className={primaryButton} disabled={upload.isPending}
                  onClick={() => fileInput.current?.click()}>
            <Upload className="h-4 w-4" aria-hidden />
            {upload.isPending ? 'Loading…' : 'Upload a spreadsheet'}
          </button>
        </div>
        <p className="mt-2 text-xs text-neutral-500">
          Fill in the template and save it as CSV. Write the area as its full path, as it reads
          in the area picker — for example “Production/103 – Dryer/Line 1” — because several
          areas share a name. A System that is not on the list yet is added to it. Rows are
          matched on asset number, so uploading a corrected sheet updates rather than duplicates.
        </p>
        {result && (
          <div className="mt-2 rounded-lg bg-white p-2.5 text-xs">
            <p className="font-medium text-neutral-800">
              {result.created} added · {result.updated} updated
              {result.skipped > 0 && ` · ${result.skipped} skipped (no name)`}
            </p>
            {result.new_systems.length > 0 && (
              <p className="mt-1 text-neutral-700">
                New Systems added to the list: {result.new_systems.join(', ')}
              </p>
            )}
            {result.problems.map((p, i) => (
              <p key={i} className="mt-1 text-warning-700">{p}</p>
            ))}
          </div>
        )}
        {error && <p className="mt-2 text-xs text-danger-700">{error}</p>}
      </div>
    </div>
  )
}


function SystemBox({ label, count, on, muted = false, onClick }: {
  label: string; count: number | undefined; on: boolean; muted?: boolean; onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on}
            className={`flex min-h-[64px] flex-col items-start justify-between rounded-lg border p-2.5 text-left transition-colors ${
              on ? 'border-primary-500 bg-primary-50 ring-1 ring-primary-500'
                : 'border-neutral-200 bg-white hover:border-primary-300'}`}>
      <span className={`line-clamp-2 text-xs font-medium ${muted ? 'text-neutral-500' : 'text-neutral-700'}`}>
        {label}
      </span>
      <span className="text-lg font-semibold tabular-nums text-neutral-900">
        {count === undefined ? '…' : count.toLocaleString()}
      </span>
    </button>
  )
}


/** Q1: "HSE merges them into the groups you want." Every machine moves; the
 *  emptied System is retired from the list. */
function MergePanel({ system, systems, onMerged }: {
  system: { id: string; label: string }
  systems: SystemCount[]
  onMerged: (into: Filter) => void
}) {
  const [into, setInto] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const count = systems.find((s) => s.system_id === system.id)?.count ?? 0
  const target = systems.find((s) => s.system_id === into)
  const merge = useMutation({
    mutationFn: () => api.post<{ moved: number }>(
      `/api/v1/equipment/systems/${system.id}/merge`, { into_id: into }),
    onSuccess: () => {
      setConfirming(false)
      if (target?.system_id) onMerged({ kind: 'system', id: target.system_id, label: target.label })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })
  const choices = systems.filter((s) => s.system_id && s.system_id !== system.id && s.is_active)
  return (
    <div className="mb-3 flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3 text-sm sm:flex-row sm:items-center">
      <span className="text-neutral-700">
        Merge <span className="font-medium">{system.label}</span> into
      </span>
      <select className={`${inputClass} sm:max-w-xs`} value={into} aria-label="System to merge into"
              onChange={(e) => { setInto(e.target.value); setConfirming(false); setError(null) }}>
        <option value="">Choose a System…</option>
        {choices.map((s) => (
          <option key={s.system_id} value={s.system_id ?? ''}>{s.label} ({s.count})</option>
        ))}
      </select>
      {!confirming ? (
        <button type="button" className={secondaryButton} disabled={!into}
                onClick={() => setConfirming(true)}>Merge</button>
      ) : (
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-neutral-600">
            Move {count.toLocaleString()} machines to {target?.label} and retire {system.label}?
          </span>
          <button type="button" className={primaryButton} disabled={merge.isPending}
                  onClick={() => { if (!merge.isPending) merge.mutate() }}>
            {merge.isPending ? 'Merging…' : 'Yes, merge'}
          </button>
          <button type="button" className={secondaryButton} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </span>
      )}
      {error && <p className="text-xs text-danger-700">{error}</p>}
    </div>
  )
}


/**
 * One machine, added or corrected by hand. Sends every field it shows, so a
 * field cleared here is cleared; the server leaves alone what is not sent.
 */
function EquipmentForm(
  { equipment, onClose, onSaved }:
  { equipment: Equipment | null; onClose: () => void; onSaved: () => void },
) {
  const [assetNo, setAssetNo] = useState(equipment?.asset_no ?? '')
  const [name, setName] = useState(equipment?.name ?? '')
  const [description, setDescription] = useState(equipment?.description ?? '')
  const [systemId, setSystemId] = useState<string | null>(equipment?.system_id ?? null)
  const [manufacturer, setManufacturer] = useState(equipment?.manufacturer ?? '')
  const [model, setModel] = useState(equipment?.model ?? '')
  const [serial, setSerial] = useState(equipment?.serial_no ?? '')
  const [locationId, setLocationId] = useState<string | null>(equipment?.location_id ?? null)
  const [departmentId, setDepartmentId] = useState<string>(equipment?.department_id ?? '')
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const depts = useQuery({
    queryKey: ['departments'],
    queryFn: () => api.get<{ id: string; name: string }[]>('/api/v1/settings/departments'),
  })

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : String(e))
  const save = useMutation({
    mutationFn: () => {
      const body = {
        asset_no: assetNo.trim() || null,
        name: name.trim(),
        description: description.trim() || null,
        system_id: systemId,
        manufacturer: manufacturer.trim() || null,
        model: model.trim() || null,
        serial_no: serial.trim() || null,
        location_id: locationId,
        department_id: departmentId || null,
      }
      return equipment
        ? api.patch(`/api/v1/equipment/${equipment.id}`, body)
        : api.post('/api/v1/equipment', body)
    },
    onSuccess: onSaved,
    onError: fail,
  })
  const retire = useMutation({
    mutationFn: () => api.post(
      `/api/v1/equipment/${equipment!.id}/${equipment!.is_active ? 'retire' : 'reinstate'}`),
    onSuccess: onSaved,
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/equipment/${equipment!.id}`),
    onSuccess: onSaved,
    onError: (e: unknown) => { setConfirmDelete(false); fail(e) },
  })
  const busy = save.isPending || retire.isPending || remove.isPending

  return (
    <Card className="mb-4">
      <SectionTitle className="mb-2">
        {equipment ? `Edit ${equipment.name}` : 'Add a machine'}
      </SectionTitle>
      {equipment && !equipment.is_active && (
        <p className="mb-3 rounded-lg bg-neutral-100 px-3 py-2 text-xs text-neutral-700">
          Retired{equipment.retired_at ? ` on ${new Date(equipment.retired_at).toLocaleDateString()}` : ''}
          {equipment.retired_by_name ? ` by ${equipment.retired_by_name}` : ''}. It is not offered on
          new forms; the records that name it still show it.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Line 1 DSI" />
        </Field>
        <Field label="Asset number" hint="Optional, but it is what a spreadsheet matches on">
          <input className={inputClass} value={assetNo}
                 onChange={(e) => setAssetNo(e.target.value)} />
        </Field>
        <Field label="System" hint="The list is kept under Settings → Lists → Equipment System">
          <VocabularySelect vocabulary="equipment_system" value={systemId}
                            currentLabel={equipment?.system_label}
                            onChange={(item) => setSystemId(item?.id ?? null)}
                            placeholder="No System" />
        </Field>
        <Field label="Where it is" hint="Forms filed in this area offer this machine">
          <LocationSelect value={locationId} onChange={setLocationId}
                          placeholder="Pick an area" />
        </Field>
        <Field label="Department it belongs to">
          <select className={inputClass} value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}>
            <option value="">None</option>
            {equipment?.department_id && !depts.data?.some((d) => d.id === equipment.department_id) && (
              <option value={equipment.department_id}>{equipment.department_name ?? 'Current department'}</option>
            )}
            {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        <Field label="Manufacturer">
          <input className={inputClass} value={manufacturer}
                 onChange={(e) => setManufacturer(e.target.value)} />
        </Field>
        <Field label="Model">
          <input className={inputClass} value={model} onChange={(e) => setModel(e.target.value)} />
        </Field>
        <Field label="Serial number">
          <input className={inputClass} value={serial} onChange={(e) => setSerial(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Description">
            <textarea rows={2} className={`${inputClass} py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </div>
      {error && <ErrorNote error={new Error(error)} />}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {equipment && (
            <button type="button" className={secondaryButton} disabled={busy}
                    onClick={() => { if (!busy) { setError(null); retire.mutate() } }}>
              {equipment.is_active ? 'Retire' : 'Put back in service'}
            </button>
          )}
          {equipment && !confirmDelete && (
            <button type="button" disabled={busy}
                    onClick={() => { setError(null); setConfirmDelete(true) }}
                    className="min-h-[44px] px-2 text-sm font-medium text-danger-700 hover:underline disabled:opacity-50">
              Delete
            </button>
          )}
          {equipment && confirmDelete && (
            <span className="flex flex-wrap items-center gap-2 text-xs text-neutral-700">
              Delete for good? Only possible when no record names it.
              <button type="button" disabled={busy}
                      onClick={() => { if (!busy) remove.mutate() }}
                      className="min-h-[36px] rounded-md bg-danger-600 px-3 font-semibold text-white disabled:opacity-50">
                {remove.isPending ? 'Deleting…' : 'Delete'}
              </button>
              <button type="button" className="min-h-[36px] px-2 font-medium text-neutral-600"
                      onClick={() => setConfirmDelete(false)}>Keep it</button>
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButton}
                  disabled={!name.trim() || busy}
                  onClick={() => { if (name.trim() && !busy) { setError(null); save.mutate() } }}>
            {save.isPending ? 'Saving…' : equipment ? 'Save' : 'Add it'}
          </button>
        </div>
      </div>
    </Card>
  )
}
