/**
 * The chemical inventory and its safety data sheets.
 *
 * WHMIS 2015 decides the shape of this page. An employer keeps an inventory of
 * the hazardous products on site, holds a current data sheet for each, makes
 * that sheet available to workers, and does not let one go three years without
 * review. Every role can open this page, because a worker's access to the
 * sheet for a product they handle is a right rather than a convenience.
 *
 * So the page opens with the two things that are findings — a product with no
 * sheet at all, and a sheet past its review — rather than with the inventory.
 * The inventory is a reference; those two are work, and neither is visible in
 * a list sorted by name.
 *
 * Search is prominent for the same reason the QR codes exist: the questions
 * actually asked of this are "what is in this drum" and "what is stored in
 * here", and both are asked standing in front of it.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Download, FileText, FlaskConical, Plus, Printer,
  Search, Sparkles, Upload, X, QrCode } from 'lucide-react'
import { api, apiBlob } from '@/lib/api'
import QrPanel from '@/components/QrPanel'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { LocationSelect } from '@/components/LocationSelect'
import { VocabularyMultiSelect } from '@/components/VocabularyPicker'
import {
  Card, Empty, ErrorNote, Field, PageHeader, Tabs,
  inputClass, primaryButton, secondaryButton,
} from '@/components/ui'

interface Sheet {
  id: string; chemical_id: string; revision: string | null
  revision_date: string | null; received_on: string | null; review_due_on: string | null
  file_id: string | null; filename: string | null; language: string
  is_current: boolean; qr_token: string | null; uploaded_by_name: string | null
}
interface Chemical {
  id: string; chemical_no: string; name: string; product_name: string | null
  manufacturer: string | null; supplier: string | null; product_code: string | null
  hazard_classes: { id: string | null; label: string }[]
  signal_word: string | null; physical_state: string | null
  location_id: string | null; location_path: string | null; department_name: string | null
  storage_note: string | null; container_size: string | null
  quantity_on_site: string | null; unit: string | null
  ppe_required: string | null; first_aid_note: string | null; notes: string | null
  is_active: boolean; current_sheet: Sheet | null
}
interface Label {
  id: string; chemical_id: string; product_identifier: string
  supplier: string | null
  signal_word: string | null; pictograms: string[]
  first_aid: string | null; ppe_note: string | null
  hazard_statements: { code: string | null; text: string }[]
  precautionary_statements: { code: string | null; text: string }[]
  source: string; status: string; approved_by_name: string | null
  print_count: number; missing: string[]
}
interface Extraction {
  available: boolean; applied: boolean
  check_closely: string[]; note: string | null
}
interface Gaps {
  products: number; with_current_sheet: number
  no_sheet: { id: string; chemical_no: string; name: string; location_path: string | null }[]
  review_overdue: {
    id: string; chemical_no: string; name: string; location_path: string | null
    review_due_on: string; days_overdue: number
  }[]
}

const TABS = [
  { key: 'gaps', label: 'Needs attention' },
  { key: 'inventory', label: 'Inventory' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function ChemicalsPage() {
  const { can } = usePermissions()
  const mayManage = can('ehs.document.manage')
  const [tab, setTab] = useState<TabKey>('gaps')

  const gaps = useQuery({
    queryKey: ['chemical-gaps'],
    queryFn: () => api.get<Gaps>('/api/v1/chemicals/gaps'),
  })

  const outstanding =
    (gaps.data?.no_sheet.length ?? 0) + (gaps.data?.review_overdue.length ?? 0)
  const tabs = TABS.map((t) =>
    t.key === 'gaps' ? { ...t, count: outstanding } : t)

  return (
    <div className="p-4">
      <PageHeader
        title="Chemicals and data sheets"
        subtitle="WHMIS 2015 — every hazardous product on site, with a current sheet a worker can reach"
      />
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === 'gaps' && <GapsTab gaps={gaps.data} loading={gaps.isLoading} />}
      {tab === 'inventory' && <InventoryTab mayManage={mayManage} />}
    </div>
  )
}

function GapsTab({ gaps, loading }: { gaps: Gaps | undefined; loading: boolean }) {
  if (loading) return <p className="text-sm text-neutral-500">Loading…</p>
  if (!gaps) return null

  const clean = gaps.no_sheet.length === 0 && gaps.review_overdue.length === 0

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-2">
        <Stat label="Products on site" value={String(gaps.products)} />
        <Stat label="With a current sheet"
              value={`${gaps.with_current_sheet} of ${gaps.products}`}
              tone={gaps.with_current_sheet === gaps.products ? 'ok' : 'warn'} />
      </div>

      {clean && (
        <p className="rounded-xl border border-success-500 bg-success-50 p-4 text-sm text-success-700">
          Every product on site has a current data sheet, and none is past its
          three-year review.
        </p>
      )}

      {gaps.no_sheet.length > 0 && (
        <section className="mb-5">
          <h2 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-danger-700">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            No data sheet at all ({gaps.no_sheet.length})
          </h2>
          <p className="mb-2 max-w-[66ch] text-xs text-neutral-600">
            A hazardous product on site with no sheet is the finding an inspector
            writes up first, and a worker who handles it has nothing to read.
          </p>
          <ul className="flex flex-col gap-1.5">
            {gaps.no_sheet.map((c) => (
              <li key={c.id}>
                <Card className="!border-danger-300 !p-3">
                  <p className="text-sm font-semibold text-neutral-900">{c.name}</p>
                  <p className="text-xs text-neutral-500">
                    {c.chemical_no}{c.location_path && ` · ${c.location_path}`}
                  </p>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}

      {gaps.review_overdue.length > 0 && (
        <section>
          <h2 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-warning-700">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            Past its three-year review ({gaps.review_overdue.length})
          </h2>
          <p className="mb-2 max-w-[66ch] text-xs text-neutral-600">
            Reg 860 gives three years from the supplier&rsquo;s revision date. Ask the
            supplier for the current sheet and file it here — the old one is kept.
          </p>
          <ul className="flex flex-col gap-1.5">
            {gaps.review_overdue.map((c) => (
              <li key={c.id}>
                <Card className="!border-warning-300 !p-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span>
                      <span className="block text-sm font-semibold text-neutral-900">
                        {c.name}
                      </span>
                      <span className="text-xs text-neutral-500">
                        {c.chemical_no}{c.location_path && ` · ${c.location_path}`}
                      </span>
                    </span>
                    <span className="text-xs font-semibold text-warning-700">
                      due {formatDate(c.review_due_on)} · {c.days_overdue} days ago
                    </span>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function InventoryTab({ mayManage }: { mayManage: boolean }) {
  const [search, setSearch] = useState('')
  const [locationId, setLocationId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  // A product that has left the plant stays on the record — an incident two
  // years old may name it — but it is not what somebody standing in front of
  // a drum is looking for, so it is off the list until asked for.
  const [showRetired, setShowRetired] = useState(false)

  const query = [
    search.trim() ? `search=${encodeURIComponent(search.trim())}` : '',
    locationId ? `location_id=${locationId}` : '',
    showRetired ? 'include_retired=true' : '',
  ].filter(Boolean).join('&')

  const chemicals = useQuery({
    queryKey: ['chemicals', search.trim(), locationId, showRetired],
    queryFn: () => api.get<Chemical[]>(`/api/v1/chemicals${query ? `?${query}` : ''}`),
  })

  return (
    <>
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                  aria-hidden />
          <input className={`${inputClass} pl-8`} value={search}
                 onChange={(e) => setSearch(e.target.value)}
                 placeholder="Name, product or supplier code" />
        </div>
        <div className="min-w-0 sm:w-72">
          <LocationSelect value={locationId} onChange={(id) => setLocationId(id)}
                          placeholder="Anywhere in the plant" />
        </div>
        {mayManage && (
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add
          </button>
        )}
      </div>

      <label className="mb-3 flex items-center gap-2 text-xs text-neutral-600">
        <input type="checkbox" checked={showRetired}
               onChange={(e) => setShowRetired(e.target.checked)} />
        Include products no longer kept on site
      </label>

      {creating && <ChemicalForm onClose={() => setCreating(false)} />}

      {chemicals.data?.length === 0 && !creating ? (
        <Empty>
          {search || locationId
            ? 'Nothing matches. Try the product name the supplier uses.'
            : 'The inventory is empty. Every hazardous product kept on site belongs here, with its data sheet.'}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {chemicals.data?.map((c) => (
            <li key={c.id}>
              <Card className="!p-0">
                <button type="button" onClick={() => setOpen(open === c.id ? null : c.id)}
                        className="flex w-full items-start gap-3 p-3.5 text-left">
                  <FlaskConical className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{c.name}</span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {c.chemical_no}
                      {c.manufacturer && ` · ${c.manufacturer}`}
                      {c.location_path && ` · ${c.location_path}`}
                    </span>
                    {c.hazard_classes.length > 0 && (
                      <span className="mt-1.5 flex flex-wrap gap-1">
                        {c.signal_word && (
                          <span className={
                            'rounded-full px-2 py-0.5 text-[11px] font-bold uppercase ' +
                            (c.signal_word === 'Danger'
                              ? 'bg-danger-600 text-white'
                              : 'bg-warning-50 text-warning-700')
                          }>
                            {c.signal_word}
                          </span>
                        )}
                        {c.hazard_classes.map((h) => (
                          <span key={h.label}
                                className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-700">
                            {h.label}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                  <SheetPill sheet={c.current_sheet} />
                </button>

                {open === c.id && <Detail chemical={c} mayManage={mayManage} />}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function SheetPill({ sheet }: { sheet: Sheet | null }) {
  if (!sheet) {
    return (
      <span className="shrink-0 rounded-full bg-danger-50 px-2 py-0.5 text-[11px] font-semibold text-danger-700">
        No data sheet
      </span>
    )
  }
  const overdue = sheet.review_due_on && new Date(sheet.review_due_on + 'T00:00:00') < new Date()
  return (
    <span className={
      'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ' +
      (overdue ? 'bg-warning-50 text-warning-700' : 'bg-success-50 text-success-700')
    }>
      {overdue ? 'Review overdue' : `Rev ${sheet.revision ?? '—'}`}
    </span>
  )
}

/**
 * Taking a product off the inventory.
 *
 * The endpoint was there from the start with nothing on screen reaching it,
 * so a drum that left the plant stayed on the list of what is here — and the
 * WHMIS inventory is meant to be an answer to "what is on this site", which a
 * list padded with products nobody stocks any more is not. It is not a delete:
 * an incident or an exposure record from two years ago may name this product,
 * and the data sheet in force at the time has to stay reachable from it.
 */
function RetireButton({ chemical }: { chemical: Chemical }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const retire = useMutation({
    mutationFn: () => api.post(`/api/v1/chemicals/${chemical.id}/retire`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chemicals'] })
      queryClient.invalidateQueries({ queryKey: ['chemical-gaps'] })
      setConfirming(false)
    },
  })

  if (!chemical.is_active) {
    return (
      <span className="self-center rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-semibold text-neutral-600">
        No longer kept on site
      </span>
    )
  }

  if (!confirming) {
    return (
      <button type="button" className={secondaryButton} onClick={() => setConfirming(true)}>
        No longer on site
      </button>
    )
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-neutral-600">
        Take {chemical.name} off the inventory? Past records keep it.
      </span>
      <button type="button" className={secondaryButton}
              onClick={() => setConfirming(false)}>Cancel</button>
      <button type="button" className={primaryButton} disabled={retire.isPending}
              onClick={() => { if (!retire.isPending) retire.mutate() }}>
        {retire.isPending ? 'Removing…' : 'Take it off'}
      </button>
    </span>
  )
}

function Detail({ chemical, mayManage }: { chemical: Chemical; mayManage: boolean }) {
  const [editing, setEditing] = useState(false)
  const [sheetCode, setSheetCode] = useState<string | null>(null)
  const sheets = useQuery({
    queryKey: ['chemical-sds', chemical.id],
    queryFn: () => api.get<Sheet[]>(`/api/v1/chemicals/${chemical.id}/sds`),
  })

  if (editing) {
    return (
      <div className="border-t border-neutral-100 p-3.5">
        <ChemicalForm existing={chemical} onClose={() => setEditing(false)} />
      </div>
    )
  }

  return (
    <div className="border-t border-neutral-100 p-3.5">
      {mayManage && (
        <div className="mb-3 flex flex-wrap justify-end gap-2">
          <RetireButton chemical={chemical} />
          <button type="button" className={secondaryButton} onClick={() => setEditing(true)}>
            Correct this product
          </button>
        </div>
      )}
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        <Row label="Container">
          {chemical.container_size ?? '—'}
          {chemical.quantity_on_site &&
            ` · ${Number(chemical.quantity_on_site)} ${chemical.unit ?? ''} on site`}
        </Row>
        <Row label="Stored">{chemical.storage_note ?? chemical.location_path ?? '—'}</Row>
        <Row label="Supplier">{chemical.supplier ?? chemical.manufacturer ?? '—'}</Row>
        <Row label="Product code">{chemical.product_code ?? '—'}</Row>
      </dl>

      {chemical.ppe_required && (
        <p className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-800">
          <span className="font-semibold">PPE:</span> {chemical.ppe_required}
        </p>
      )}
      {chemical.first_aid_note && (
        <p className="mt-1.5 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          <span className="font-semibold">First aid:</span> {chemical.first_aid_note}
        </p>
      )}

      <h3 className="mb-1.5 mt-4 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Data sheets
      </h3>
      {sheets.data?.length === 0 ? (
        <p className="text-sm text-neutral-500">None filed.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {sheets.data?.map((s) => (
            <li key={s.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-neutral-200 px-3 py-2">
              <FileText className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
              <span className="min-w-0 flex-1 text-sm text-neutral-800">
                Revision {s.revision ?? '—'}
                {s.revision_date && ` · ${formatDate(s.revision_date)}`}
              </span>
              {s.is_current
                ? <span className="rounded-full bg-success-50 px-2 py-0.5 text-[11px] font-semibold text-success-700">
                    Current
                  </span>
                : <span className="text-[11px] text-neutral-400">Superseded</span>}
              {s.review_due_on && s.is_current && (
                <span className="text-xs text-neutral-500">
                  review due {formatDate(s.review_due_on)}
                </span>
              )}
              <SheetActions sheet={s} chemicalId={chemical.id} mayManage={mayManage} />
              {s.is_current && s.qr_token && (
                <button
                  type="button"
                  onClick={() => setSheetCode(sheetCode === s.id ? null : s.id)}
                  aria-expanded={sheetCode === s.id}
                  title="Show the code to print for this data sheet"
                  className="flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800"
                >
                  <QrCode className="h-3.5 w-3.5" aria-hidden />
                  Code
                </button>
              )}
              {sheetCode === s.id && (
                <div className="w-full border-t border-neutral-100 pt-2.5">
                  <QrPanel
                    token={s.qr_token}
                    caption="Scan for this product's data sheet"
                    sub={`${chemical.name} · rev ${s.revision ?? '—'}`}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {mayManage && <NewSheetForm chemicalId={chemical.id} />}

      <LabelPanel chemical={chemical} mayManage={mayManage} />
    </div>
  )
}

/**
 * The workplace label for this product.
 *
 * WHMIS requires one on any container the product is decanted into, and the
 * information all sits on the supplier's data sheet — the work is transcribing
 * it. Reading the sheet automatically is an assist and is allowed to be
 * unavailable; what is not optional is the approval, which is why the print
 * button only appears once somebody has approved the draft.
 */
/**
 * Correcting a workplace label by hand.
 *
 * Reading it off the data sheet gets most of it and is the fast path, but it
 * is a reading of a PDF: a scanned sheet gives nothing, a statement comes back
 * truncated, a hazard class is missed. Until now there was no way to fix any
 * of that — the only buttons were read, approve and print — so a label the
 * reader got wrong was a label that stayed wrong, and WHMIS is a standard
 * about what the container actually says.
 *
 * Draft only. An approved label is a record of wording somebody signed off;
 * changing it means approving it again, which the status gate already
 * enforces on the server.
 */
function LabelEditor({ label, onClose }: { label: Label; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [productIdentifier, setProductIdentifier] = useState(label.product_identifier)
  const [supplier, setSupplier] = useState(label.supplier ?? '')
  const [signalWord, setSignalWord] = useState(label.signal_word ?? '')
  const [pictograms, setPictograms] = useState<string[]>(label.pictograms ?? [])
  const [hazards, setHazards] = useState(label.hazard_statements ?? [])
  const [precautions, setPrecautions] = useState(label.precautionary_statements ?? [])
  const [firstAid, setFirstAid] = useState(label.first_aid ?? '')
  const [ppe, setPpe] = useState(label.ppe_note ?? '')

  const classes = useQuery({
    queryKey: ['vocabulary', 'ghs_hazard_class'],
    queryFn: () => api.get<{ id: string; code: string; label: string }[]>(
      '/api/v1/settings/vocabularies/ghs_hazard_class/items'),
  })

  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/labels/${label.id}`, {
      product_identifier: productIdentifier,
      supplier: supplier || null,
      signal_word: signalWord || null,
      pictograms,
      // Blank rows are the ordinary result of adding one and changing your
      // mind; sending them would put an empty bullet on a printed label.
      hazard_statements: hazards.filter((h) => h.text.trim()),
      precautionary_statements: precautions.filter((p) => p.text.trim()),
      first_aid: firstAid || null,
      ppe_note: ppe || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chemical-labels', label.chemical_id] })
      onClose()
    },
  })

  return (
    <div className="mt-3 rounded-xl border border-neutral-200 bg-white p-3.5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-neutral-900">The wording on the label</h3>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Product identifier"
               hint="Has to match the name on the data sheet — that is what WHMIS ties together">
          <input className={inputClass} value={productIdentifier}
                 onChange={(e) => setProductIdentifier(e.target.value)} />
        </Field>
        <Field label="Supplier">
          <input className={inputClass} value={supplier}
                 onChange={(e) => setSupplier(e.target.value)} />
        </Field>
        <Field label="Signal word" hint="GHS allows only these two, or neither">
          <select className={inputClass} value={signalWord}
                  onChange={(e) => setSignalWord(e.target.value)}>
            <option value="">None</option>
            <option value="Danger">Danger</option>
            <option value="Warning">Warning</option>
          </select>
        </Field>
      </div>

      <div className="mt-3">
        <Field label="Pictograms"
               hint="The artwork is uploaded once per class in Settings; a class with none cannot be approved">
          <div className="flex flex-wrap gap-1.5">
            {classes.data?.map((c) => {
              const on = pictograms.some(
                (p) => p.toLowerCase() === c.code.toLowerCase()
                    || p.toLowerCase() === c.label.toLowerCase())
              return (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setPictograms(on
                    ? pictograms.filter((p) => p.toLowerCase() !== c.code.toLowerCase()
                                            && p.toLowerCase() !== c.label.toLowerCase())
                    : [...pictograms, c.code])}
                  className={`min-h-[32px] rounded-full border px-2.5 text-xs font-medium ${
                    on ? 'border-primary-300 bg-primary-50 text-primary-800'
                       : 'border-neutral-200 bg-white text-neutral-600'}`}
                >
                  {c.label}
                </button>
              )
            })}
          </div>
        </Field>
      </div>

      <StatementRows title="Hazard statements" rows={hazards} onChange={setHazards}
                     placeholder="Causes severe skin burns and eye damage." />
      <StatementRows title="Precautionary statements" rows={precautions}
                     onChange={setPrecautions}
                     placeholder="Wear protective gloves and eye protection." />

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="First aid">
          <textarea className={`${inputClass} min-h-[60px] py-2`} value={firstAid}
                    onChange={(e) => setFirstAid(e.target.value)} />
        </Field>
        <Field label="PPE">
          <textarea className={`${inputClass} min-h-[60px] py-2`} value={ppe}
                    onChange={(e) => setPpe(e.target.value)} />
        </Field>
      </div>

      <ErrorNote error={save.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!productIdentifier.trim() || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save the wording'}
        </button>
      </div>
    </div>
  )
}

/** A GHS statement is a code and its text, and the code is optional — plenty
 *  of workplace labels carry the sentence without the H-number. */
function StatementRows({ title, rows, onChange, placeholder }: {
  title: string
  rows: { code: string | null; text: string }[]
  onChange: (rows: { code: string | null; text: string }[]) => void
  placeholder: string
}) {
  const set = (i: number, patch: Partial<{ code: string | null; text: string }>) =>
    onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)))

  return (
    <div className="mt-3">
      <p className="mb-1 text-xs font-medium text-neutral-700">{title}</p>
      <div className="flex flex-col gap-1.5">
        {rows.map((r, i) => (
          <div key={i} className="flex gap-1.5">
            <input className={`${inputClass} w-32 shrink-0`} value={r.code ?? ''}
                   onChange={(e) => set(i, { code: e.target.value || null })}
                   placeholder="H314" aria-label={`${title} code`} />
            <input className={inputClass} value={r.text}
                   onChange={(e) => set(i, { text: e.target.value })}
                   placeholder={placeholder} aria-label={`${title} text`} />
            <button type="button" aria-label="Remove"
                    onClick={() => onChange(rows.filter((_, k) => k !== i))}
                    className="shrink-0 rounded p-1 text-neutral-400 hover:bg-neutral-50">
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
      <button type="button"
              onClick={() => onChange([...rows, { code: null, text: '' }])}
              className="mt-1.5 text-xs font-semibold text-primary-700 hover:underline">
        Add one
      </button>
    </div>
  )
}

function LabelPanel({ chemical, mayManage }: { chemical: Chemical; mayManage: boolean }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [editingLabel, setEditingLabel] = useState(false)

  const labels = useQuery({
    queryKey: ['chemical-labels', chemical.id],
    queryFn: () => api.get<Label[]>(`/api/v1/labels?chemical_id=${chemical.id}`),
  })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['chemical-labels', chemical.id] })

  const draft = useMutation({
    mutationFn: () => api.post(`/api/v1/chemicals/${chemical.id}/labels`, {}),
    onSuccess: invalidate,
  })
  const read = useMutation({
    mutationFn: (labelId: string) =>
      api.post<Extraction>(`/api/v1/labels/${labelId}/read-sds`, {}),
    onSuccess: invalidate,
  })
  const approve = useMutation({
    mutationFn: (labelId: string) => api.post(`/api/v1/labels/${labelId}/approve`, {}),
    onSuccess: invalidate,
  })

  const current = (labels.data ?? []).find(
    (l) => l.status === 'draft' || l.status === 'approved' || l.status === 'printed')

  const print = async (labelId: string) => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await apiBlob(`/api/v1/labels/${labelId}/pdf?copies=2`)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
      invalidate()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4">
      <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Workplace label
      </h3>

      {!current ? (
        <>
          <p className="mb-2 max-w-[60ch] text-sm text-neutral-600">
            Needed on any container this is decanted into. A draft starts from
            what is already recorded here; the hazard statements come off the
            data sheet.
          </p>
          {mayManage && (
            <button type="button" className={secondaryButton} disabled={draft.isPending}
                    onClick={() => { if (!draft.isPending) draft.mutate() }}>
              <Plus className="h-4 w-4" aria-hidden />
              {draft.isPending ? 'Starting…' : 'Start a label'}
            </button>
          )}
        </>
      ) : (
        <div className="rounded-lg border border-neutral-200 p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="min-w-0 flex-1 text-sm font-medium text-neutral-900">
              {current.product_identifier}
            </span>
            <span className={
              'rounded-full px-2 py-0.5 text-[11px] font-semibold ' +
              (current.status === 'draft' ? 'bg-warning-50 text-warning-700'
                : 'bg-success-50 text-success-700')
            }>
              {current.status === 'draft' ? 'Draft — not approved'
                : current.status === 'printed'
                  ? `Approved · printed ${current.print_count}×` : 'Approved'}
            </span>
          </div>

          <p className="mt-1 text-xs text-neutral-500">
            {current.signal_word ?? 'No signal word'} ·
            {' '}{current.pictograms.length} pictogram
            {current.pictograms.length === 1 ? '' : 's'} ·
            {' '}{current.hazard_statements.length} hazard statement
            {current.hazard_statements.length === 1 ? '' : 's'}
          </p>

          {current.missing.length > 0 && (
            <p className="mt-1.5 rounded-lg bg-warning-50 px-2.5 py-1.5 text-xs text-warning-700">
              Not yet a label — missing {current.missing.join(' and ')}.
            </p>
          )}

          {read.data && !read.data.available && (
            <p className="mt-1.5 rounded-lg bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-600">
              {read.data.note}
            </p>
          )}
          {read.data?.applied && (
            <p className="mt-1.5 rounded-lg bg-primary-50 px-2.5 py-1.5 text-xs text-primary-800">
              {read.data.note}
              {read.data.check_closely.length > 0 && (
                <> Look hardest at: {read.data.check_closely.join(', ')}.</>
              )}
            </p>
          )}

          <ErrorNote error={draft.error || read.error || approve.error} />

          {mayManage && editingLabel && (
            <LabelEditor label={current} onClose={() => setEditingLabel(false)} />
          )}

          {mayManage && !editingLabel && (
            <div className="mt-2 flex flex-wrap gap-2">
              {current.status === 'draft' && (
                <>
                  <button type="button" className={secondaryButton}
                          onClick={() => setEditingLabel(true)}>
                    Edit the wording
                  </button>
                  <button type="button" className={secondaryButton}
                          disabled={read.isPending}
                          onClick={() => { if (!read.isPending) read.mutate(current.id) }}>
                    <Sparkles className="h-4 w-4" aria-hidden />
                    {read.isPending ? 'Reading the sheet…' : 'Read it off the data sheet'}
                  </button>
                  <button type="button" className={primaryButton}
                          disabled={current.missing.length > 0 || approve.isPending}
                          onClick={() => { if (!approve.isPending) approve.mutate(current.id) }}>
                    Approve it
                  </button>
                </>
              )}
              {(current.status === 'approved' || current.status === 'printed') && (
                <button type="button" className={primaryButton} disabled={busy}
                        onClick={() => print(current.id)}>
                  <Printer className="h-4 w-4" aria-hidden />
                  {busy ? 'Opening…' : 'Print'}
                </button>
              )}
            </div>
          )}
          {current.status === 'draft' && (
            <p className="mt-1.5 text-xs text-neutral-500">
              Nothing prints until somebody approves it — a container labelled
              with the wrong pictogram is worse than one with none.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="text-sm text-neutral-900">{children}</dd>
    </div>
  )
}

function SheetActions(
  { sheet, chemicalId, mayManage }:
  { sheet: Sheet; chemicalId: string; mayManage: boolean },
) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.upload(`/api/v1/sds/${sheet.id}/file`, form)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chemical-sds', chemicalId] })
      queryClient.invalidateQueries({ queryKey: ['chemicals'] })
    },
  })

  // Proxied by ehs-api so the request carries a bearer token, which a plain
  // <a href> cannot send.
  const open = async () => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await apiBlob(`/api/v1/sds/${sheet.id}/file`)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } finally {
      setBusy(false)
    }
  }

  if (sheet.file_id) {
    return (
      <button type="button" onClick={open} disabled={busy}
              className="flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline">
        <Download className="h-3.5 w-3.5" aria-hidden />
        {busy ? 'Opening…' : 'Open'}
      </button>
    )
  }
  if (!mayManage) {
    return <span className="text-xs text-danger-700">No file attached</span>
  }
  return (
    <label className="flex cursor-pointer items-center gap-1 text-xs font-semibold text-primary-700">
      <Upload className="h-3.5 w-3.5" aria-hidden />
      {upload.isPending ? 'Uploading…' : 'Attach the file'}
      <input type="file" className="sr-only" accept=".pdf,image/*"
             onChange={(e) => {
               const file = e.target.files?.[0]
               if (file) upload.mutate(file)
               e.target.value = ''
             }} />
    </label>
  )
}

function NewSheetForm({ chemicalId }: { chemicalId: string }) {
  const queryClient = useQueryClient()
  const [openForm, setOpenForm] = useState(false)
  const [revision, setRevision] = useState('')
  const [revisionDate, setRevisionDate] = useState('')

  const create = useMutation({
    mutationFn: () => api.post(`/api/v1/chemicals/${chemicalId}/sds`, {
      revision: revision || null,
      revision_date: revisionDate || null,
    }),
    onSuccess: () => {
      setOpenForm(false); setRevision(''); setRevisionDate('')
      queryClient.invalidateQueries({ queryKey: ['chemical-sds', chemicalId] })
      queryClient.invalidateQueries({ queryKey: ['chemicals'] })
      queryClient.invalidateQueries({ queryKey: ['chemical-gaps'] })
    },
  })

  if (!openForm) {
    return (
      <button type="button" className={`${secondaryButton} mt-2`}
              onClick={() => setOpenForm(true)}>
        <Plus className="h-4 w-4" aria-hidden />
        File a revision
      </button>
    )
  }

  return (
    <div className="mt-2 rounded-lg border border-neutral-200 p-2.5">
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Revision">
          <input className={inputClass} value={revision}
                 onChange={(e) => setRevision(e.target.value)} placeholder="4.1" />
        </Field>
        <Field label="Supplier's revision date"
               hint="The three years run from this date, not from today">
          <input type="date" className={inputClass} value={revisionDate}
                 onChange={(e) => setRevisionDate(e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" className={secondaryButton}
                onClick={() => setOpenForm(false)}>Cancel</button>
        <button type="button" className={primaryButton} disabled={create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Filing…' : 'File it'}
        </button>
      </div>
      <p className="mt-1.5 text-xs text-neutral-500">
        The sheet this replaces is kept — it was the one in force at the time of
        anything that happened while it stood.
      </p>
    </div>
  )
}

/**
 * Adding a product and correcting one are the same form.
 *
 * Correcting was missing: the endpoint had been there all along and nothing on
 * screen reached it, so a product entered with a typo in its name stayed that
 * way — and the name is the product identifier printed on the label, which
 * WHMIS requires to match the data sheet. Two forms would have drifted, so
 * there is one, and `existing` decides whether it creates or corrects.
 */
function ChemicalForm({ existing, onClose }: { existing?: Chemical; onClose: () => void }) {
  const queryClient = useQueryClient()
  const editing = Boolean(existing)
  const [name, setName] = useState(existing?.name ?? '')
  const [manufacturer, setManufacturer] = useState(existing?.manufacturer ?? '')
  const [productCode, setProductCode] = useState(existing?.product_code ?? '')
  const [locationId, setLocationId] = useState<string | null>(existing?.location_id ?? null)
  const [hazardIds, setHazardIds] = useState<string[]>(
    () => (existing?.hazard_classes ?? []).map((h) => h.id).filter((x): x is string => Boolean(x)))
  const [hazardLabels, setHazardLabels] = useState<Record<string, string>>(
    () => Object.fromEntries((existing?.hazard_classes ?? [])
      .filter((h) => h.id).map((h) => [h.id as string, h.label])))
  const [signalWord, setSignalWord] = useState(existing?.signal_word ?? '')
  const [container, setContainer] = useState(existing?.container_size ?? '')
  const [ppe, setPpe] = useState(existing?.ppe_required ?? '')
  const [firstAid, setFirstAid] = useState(existing?.first_aid_note ?? '')

  const body = () => ({
    name,
    manufacturer: manufacturer || null,
    product_code: productCode || null,
    location_id: locationId,
    hazard_classes: hazardIds.map((id) => ({ id, label: hazardLabels[id] ?? '' })),
    signal_word: signalWord || null,
    container_size: container || null,
    ppe_required: ppe || null,
    first_aid_note: firstAid || null,
  })

  const create = useMutation({
    mutationFn: () => (editing
      ? api.patch(`/api/v1/chemicals/${existing!.id}`, body())
      : api.post('/api/v1/chemicals', body())),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chemicals'] })
      queryClient.invalidateQueries({ queryKey: ['chemical-gaps'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">
          {editing ? 'Correct this product' : 'Add a product'}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="What it is">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Caustic soda 50%" />
        </Field>
        <Field label="Manufacturer or supplier">
          <input className={inputClass} value={manufacturer}
                 onChange={(e) => setManufacturer(e.target.value)} />
        </Field>
        <Field label="Where it is kept">
          <LocationSelect value={locationId} onChange={(id) => setLocationId(id)} />
        </Field>
        <Field label="Product code">
          <input className={inputClass} value={productCode}
                 onChange={(e) => setProductCode(e.target.value)} />
        </Field>
        <Field label="Signal word" hint="GHS allows only these two, or neither">
          <select className={inputClass} value={signalWord}
                  onChange={(e) => setSignalWord(e.target.value)}>
            <option value="">None</option>
            <option value="Danger">Danger</option>
            <option value="Warning">Warning</option>
          </select>
        </Field>
        <Field label="Container">
          <input className={inputClass} value={container}
                 onChange={(e) => setContainer(e.target.value)} placeholder="205 L drum" />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Hazard classes"
                 hint="A product is often several at once — pick all that apply">
            <VocabularyMultiSelect
              vocabulary="ghs_hazard_class"
              value={hazardIds}
              onChange={(ids, items) => {
                setHazardIds(ids)
                if (items) {
                  setHazardLabels((m) => ({
                    ...m,
                    ...Object.fromEntries(items.map((i) => [i.id, i.label])),
                  }))
                }
              }}
            />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="PPE required">
            <input className={inputClass} value={ppe} onChange={(e) => setPpe(e.target.value)}
                   placeholder="Face shield, chemical apron, nitrile gauntlets" />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="First aid"
                 hint="Shown in red on the product — it is read in a hurry">
            <input className={inputClass} value={firstAid}
                   onChange={(e) => setFirstAid(e.target.value)}
                   placeholder="Flush 15 minutes at the eyewash" />
          </Field>
        </div>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!name.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? (editing ? 'Saving…' : 'Adding…') : (editing ? 'Save' : 'Add')}
        </button>
      </div>
      {!editing && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          File its data sheet next — a product without one is the first thing an
          inspector asks about.
        </p>
      )}
      {editing && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          The name is the product identifier on the label. WHMIS requires it to
          match the data sheet, so a label already approved is withdrawn when it
          changes and has to be approved again.
        </p>
      )}
    </Card>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  const colour = tone === 'warn' ? 'text-warning-700'
    : tone === 'ok' ? 'text-success-700' : 'text-neutral-900'
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums ${colour}`}>{value}</p>
    </div>
  )
}
