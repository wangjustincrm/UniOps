/**
 * The incident types offered under "What kind of incident was this?".
 *
 * ★ Round 12, GEN-01 and Q10: "HSE can add new types and set the label, form,
 * category and who can pick them, but not the legal flags." The built-in types
 * are listed, fixed: they decide the Ministry and WSIB clocks and the injury
 * rates. A type added here sets no injury class and never tells the Ministry;
 * how badly anyone was hurt is asked on the form, as for the built-in types.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import {
  Card, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import { VocabularySelect } from '@/components/VocabularyPicker'

interface TypeRow {
  code: string; label: string; form_kind: string
  category_id: string | null; category_label: string | null
  restricted: boolean; is_active: boolean; builtin: boolean
  mol_reportable: boolean; injury_class: string | null
}

const FORMS: Record<string, string> = {
  medical: 'Someone was hurt', equipment: 'Equipment was involved', near_miss: 'Near miss',
}

export default function IncidentTypesPage() {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<TypeRow | null>(null)
  const list = useQuery({
    queryKey: ['incident-types', 'settings'],
    queryFn: () => api.get<TypeRow[]>('/api/v1/settings/incident-types'),
  })
  const done = () => {
    setAdding(false); setEditing(null)
    queryClient.invalidateQueries({ queryKey: ['incident-types'] })
  }
  const rows = list.data ?? []
  return (
    <div className="p-4">
      <PageHeader title="Incident types">
        <button type="button" className={primaryButton} onClick={() => { setAdding(true); setEditing(null) }}>
          <Plus className="h-4 w-4" aria-hidden />
          Add a type
        </button>
      </PageHeader>
      <p className="mb-4 text-sm text-neutral-600">
        What the report offers under “What kind of incident was this?”. You set a type’s name,
        which form offers it, its category and who may pick it. The built-in types are fixed,
        because they decide the Ministry and WSIB deadlines and the injury rates.
      </p>
      {(adding || editing) && (
        <TypeForm key={editing?.code ?? 'new'} row={editing} onDone={done}
                  onCancel={() => { setAdding(false); setEditing(null) }} />
      )}
      {list.isError && <ErrorNote error={list.error} />}
      {(['medical', 'equipment', 'near_miss'] as const).map((form) => (
        <section key={form} className="mb-5">
          <SectionTitle className="mb-2">{FORMS[form]}</SectionTitle>
          <ul className="flex flex-col gap-1.5">
            {rows.filter((r) => r.form_kind === form).map((r) => (
              <li key={r.code}
                  className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-neutral-200 bg-white p-3 ${r.is_active ? '' : 'opacity-60'}`}>
                <span className="min-w-0 flex-1 text-sm font-medium text-neutral-900">{r.label}</span>
                {r.category_label && <span className="text-xs text-neutral-500">{r.category_label}</span>}
                {r.restricted && (
                  <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-700">
                    Coordinators, supervisors and HSE
                  </span>
                )}
                {r.mol_reportable && (
                  <span className="rounded-full bg-danger-50 px-2 py-0.5 text-[11px] font-semibold text-danger-700">
                    Tell the Ministry
                  </span>
                )}
                {!r.is_active && (
                  <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">Retired</span>
                )}
                {r.builtin ? (
                  <span className="flex items-center gap-1 text-xs text-neutral-500">
                    <Lock className="h-3.5 w-3.5" aria-hidden /> Built in
                  </span>
                ) : (
                  <button type="button" onClick={() => { setEditing(r); setAdding(false) }}
                          className="min-h-[28px] text-xs font-medium text-primary-700 hover:underline">
                    Edit
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function TypeForm({ row, onDone, onCancel }: {
  row: TypeRow | null; onDone: () => void; onCancel: () => void
}) {
  const [label, setLabel] = useState(row?.label ?? '')
  const [form, setForm] = useState(row?.form_kind ?? 'near_miss')
  const [categoryId, setCategoryId] = useState<string | null>(row?.category_id ?? null)
  const [restricted, setRestricted] = useState(row?.restricted ?? false)
  const body = { label: label.trim(), form_kind: form, category_id: categoryId, restricted }
  const save = useMutation({
    mutationFn: (extra: Record<string, unknown> = {}) => row
      ? api.patch(`/api/v1/settings/incident-types/${row.code}`, { ...body, ...extra })
      : api.post('/api/v1/settings/incident-types', body),
    onSuccess: onDone,
  })
  const ready = label.trim().length >= 2
  return (
    <Card className="mb-4">
      <SectionTitle className="mb-2">{row ? `Edit ${row.label}` : 'Add a type'}</SectionTitle>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name, as the report shows it">
          <input className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)}
                 placeholder="Allergen cross-contact" />
        </Field>
        <Field label="Offered on">
          <select className={inputClass} value={form} onChange={(e) => setForm(e.target.value)}>
            {Object.entries(FORMS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="Category" hint="What Reports groups it under">
          <VocabularySelect vocabulary="incident_category" value={categoryId}
                            currentLabel={row?.category_label}
                            onChange={(item) => setCategoryId(item?.id ?? null)} placeholder="None" />
        </Field>
        <Field label="Who may pick it">
          <select className={inputClass} value={restricted ? 'some' : 'all'}
                  onChange={(e) => setRestricted(e.target.value === 'some')}>
            <option value="all">Everyone who can report</option>
            <option value="some">Coordinators, supervisors and HSE</option>
          </select>
        </Field>
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        It does not tell the Ministry and does not set how badly anyone was hurt; the form asks
        that, as it does for the built-in types.
      </p>
      <ErrorNote error={save.error} />
      <div className="mt-3 flex flex-wrap justify-between gap-2">
        <div>
          {row && (
            <button type="button" className={secondaryButton} disabled={save.isPending}
                    onClick={() => { if (!save.isPending) save.mutate({ is_active: !row.is_active }) }}>
              {row.is_active ? 'Retire' : 'Offer it again'}
            </button>
          )}
        </div>
        <div className="flex gap-2">
          <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
          <button type="button" className={primaryButton} disabled={!ready || save.isPending}
                  onClick={() => { if (ready && !save.isPending) save.mutate({}) }}>
            {save.isPending ? 'Saving…' : row ? 'Save' : 'Add it'}
          </button>
        </div>
      </div>
    </Card>
  )
}
