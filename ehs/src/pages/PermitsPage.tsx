/**
 * Work permits — confined space, hot work, height, lockout, excavation, live
 * electrical.
 *
 * The page is built around one question and answers it before anything else:
 * what is open right now. A permit board in a workshop is read by somebody
 * walking past who needs to know whether a space is occupied, and that has to
 * be the first thing on screen rather than something behind a filter.
 *
 * The second idea is that the system refuses rather than warns. A permit
 * cannot be issued with an unticked precaution, a failing or stale gas test, or
 * a missing signature — and the whole list of what is outstanding is shown at
 * once, because sending somebody round the loop five times is how a checklist
 * turns into a formality.
 *
 * Status shown here is always the computed one. A permit that ran out at 18:00
 * reads as expired at 18:01 whether or not the server's sweep has run; "the
 * board still says ACTIVE" is how a crew re-enters a space on yesterday's
 * authorisation.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Check, ClipboardCheck, Gauge, Plus, ShieldCheck, X,
} from 'lucide-react'
import { api } from '@/lib/api'
import QrPanel from '@/components/QrPanel'
import { formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { useMyProfile } from '@/hooks/useMyProfile'
import { LocationSelect } from '@/components/LocationSelect'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { SignaturePad } from '@/components/SignaturePad'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, Tabs,
} from '@/components/ui'

interface Precaution { key: string; label: string; help?: string }
interface PermitType {
  id: string; code: string; name: string; description: string | null
  precautions: Precaution[]; requires_gas_test: boolean
  required_signatures: string[]; max_duration_hours: number
}
interface Permit {
  id: string; permit_no: string; type_id: string | null; type_label: string
  work_description: string; location_id: string | null; location_path: string | null
  department_name: string | null; contractor_company: string | null
  requested_by_name: string | null
  valid_from: string | null; valid_to: string | null
  precautions_confirmed: Record<string, boolean>
  issued_at: string | null; issued_by_name: string | null
  suspended_reason: string | null
  closed_at: string | null; closed_by_name: string | null; closeout_note: string | null
  qr_token: string | null
  status: string
  blockers: string[]
}
interface GasReading {
  id: string; taken_at: string; taken_by_name: string | null
  oxygen_pct: string | null; lel_pct: string | null
  h2s_ppm: string | null; co_ppm: string | null
  other_note: string | null; instrument: string | null
  is_acceptable: boolean; failed: string[]; is_fresh: boolean
}
interface Signature {
  id: string; phase: string; capacity: string; person_name: string
  person_role: string | null; external_company: string | null; signed_at: string
}

const CAPACITY_LABELS: Record<string, string> = {
  issuer: 'Issuer', receiver: 'Person in charge of the work',
  attendant: 'Attendant', gas_tester: 'Gas tester', fire_watch: 'Fire watch',
}

const STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: 'Draft', tone: 'bg-neutral-100 text-neutral-600' },
  issued: { label: 'Live', tone: 'bg-success-600 text-white' },
  suspended: { label: 'Suspended', tone: 'bg-warning-50 text-warning-700' },
  expired: { label: 'Expired', tone: 'bg-danger-600 text-white' },
  closed: { label: 'Closed', tone: 'bg-neutral-100 text-neutral-600' },
  cancelled: { label: 'Cancelled', tone: 'bg-neutral-100 text-neutral-500' },
}

const TABS = [
  { key: 'live', label: 'Open now' },
  { key: 'preparing', label: 'Being prepared' },
  { key: 'history', label: 'Finished' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function PermitsPage() {
  const { can } = usePermissions()
  const mayIssue = can('ehs.permit.issue')
  const [tab, setTab] = useState<TabKey>('live')
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const permits = useQuery({
    queryKey: ['permits'],
    queryFn: () => api.get<Permit[]>('/api/v1/permits'),
    // A permit board goes stale in a way that matters. Thirty seconds is the
    // difference between "expired" showing up and somebody trusting the row.
    refetchInterval: 30_000,
  })

  const rows = permits.data ?? []
  const live = rows.filter((p) => p.status === 'issued' || p.status === 'suspended')
  const preparing = rows.filter((p) => p.status === 'draft')
  const history = rows.filter(
    (p) => p.status === 'closed' || p.status === 'expired' || p.status === 'cancelled')
  const expiredOpen = rows.filter((p) => p.status === 'expired' && !p.closed_at)

  const shown = tab === 'live' ? live : tab === 'preparing' ? preparing : history
  const tabs = TABS.map((t) => ({
    ...t,
    count: t.key === 'live' ? live.length
      : t.key === 'preparing' ? preparing.length : undefined,
  }))

  return (
    <div className="p-4">
      <PageHeader
        title="Work permits"
        subtitle="Confined space, hot work, height, lockout, excavation, live electrical"
      >
        <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" aria-hidden />
          Raise a permit
        </button>
      </PageHeader>

      {expiredOpen.length > 0 && (
        <div className="mb-4 rounded-xl border-l-4 border-danger-600 bg-danger-50 p-3.5">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-danger-700">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {expiredOpen.length} permit{expiredOpen.length === 1 ? '' : 's'} ran out
            without being closed
          </p>
          <p className="mt-0.5 text-xs text-danger-700">
            Somebody has to confirm the area was left safe. Until that happens
            nobody knows whether the space is clear.
          </p>
        </div>
      )}

      {creating && <NewPermitForm onClose={() => setCreating(false)} />}

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {shown.length === 0 ? (
        <Empty>
          {tab === 'live'
            ? 'Nothing is open. No confined space, hot work or lockout is authorised right now.'
            : tab === 'preparing'
              ? 'Nothing being prepared.'
              : 'No finished permits yet.'}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {shown.map((p) => (
            <li key={p.id}>
              <Card className={'!p-0 ' + (p.status === 'expired' && !p.closed_at
                ? '!border-danger-400' : p.status === 'suspended' ? '!border-warning-400' : '')}>
                <button type="button" onClick={() => setOpen(open === p.id ? null : p.id)}
                        className="flex w-full items-start gap-3 p-3.5 text-left">
                  <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {p.type_label}
                    </span>
                    <span className="mt-0.5 block text-xs text-neutral-500">
                      {p.permit_no}
                      {p.location_path && ` · ${p.location_path}`}
                      {p.contractor_company && ` · ${p.contractor_company}`}
                    </span>
                    <span className="mt-1 block text-sm text-neutral-700">
                      {p.work_description}
                    </span>
                    {p.valid_to && (
                      <span className="mt-0.5 block text-xs text-neutral-500">
                        {p.status === 'issued' ? 'Valid until ' : 'Ran to '}
                        {formatDateTime(p.valid_to)}
                      </span>
                    )}
                    {p.suspended_reason && (
                      <span className="mt-1 block text-xs font-medium text-warning-700">
                        Suspended: {p.suspended_reason}
                      </span>
                    )}
                  </span>
                  <StatusPill status={p.status} />
                </button>

                {open === p.id && <PermitDetail permit={p} mayIssue={mayIssue} />}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const spec = STATUS[status] ?? { label: status, tone: 'bg-neutral-100 text-neutral-600' }
  return (
    <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold ${spec.tone}`}>
      {spec.label}
    </span>
  )
}

function PermitDetail({ permit, mayIssue }: { permit: Permit; mayIssue: boolean }) {
  const queryClient = useQueryClient()
  const [closing, setClosing] = useState(false)

  const types = useQuery({
    queryKey: ['permit-types'],
    queryFn: () => api.get<PermitType[]>('/api/v1/permit-types'),
    staleTime: 30 * 60_000,
  })
  const readings = useQuery({
    queryKey: ['permit-gas', permit.id],
    queryFn: () => api.get<GasReading[]>(`/api/v1/permits/${permit.id}/gas`),
  })
  const signatures = useQuery({
    queryKey: ['permit-signatures', permit.id],
    queryFn: () => api.get<Signature[]>(`/api/v1/permits/${permit.id}/signatures`),
  })

  const type = types.data?.find((t) => t.id === permit.type_id)
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['permits'] })
    queryClient.invalidateQueries({ queryKey: ['permit-gas', permit.id] })
    queryClient.invalidateQueries({ queryKey: ['permit-signatures', permit.id] })
  }

  const issue = useMutation({
    mutationFn: () => api.post(`/api/v1/permits/${permit.id}/issue`, {}),
    onSuccess: invalidate,
  })
  const suspend = useMutation({
    mutationFn: (reason: string) =>
      api.post(`/api/v1/permits/${permit.id}/suspend`, { reason }),
    onSuccess: invalidate,
  })

  const editable = permit.status === 'draft' || permit.status === 'suspended'

  return (
    <div className="border-t border-neutral-100 p-3.5">
      {permit.blockers.length > 0 && (
        <div className="mb-4 rounded-lg border-l-3 border-warning-500 bg-warning-50 p-3">
          <p className="text-sm font-semibold text-warning-700">
            Before this can be issued
          </p>
          <ul className="mt-1 list-disc pl-5 text-sm text-warning-700">
            {permit.blockers.map((b) => <li key={b}>{b}</li>)}
          </ul>
        </div>
      )}

      {type && (
        <PrecautionList permit={permit} type={type} editable={editable}
                        onSaved={invalidate} />
      )}

      {type?.requires_gas_test && (
        <GasPanel permit={permit} readings={readings.data ?? []} onSaved={invalidate} />
      )}

      <SignaturePanel permit={permit} type={type} signatures={signatures.data ?? []}
                      editable={editable} onSaved={invalidate} />

      {permit.issued_at && (
        <p className="mt-3 text-xs text-neutral-500">
          Issued {formatDateTime(permit.issued_at)} by {permit.issued_by_name}
        </p>
      )}
      {permit.closed_at && (
        <p className="mt-1 rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
          Closed {formatDateTime(permit.closed_at)} by {permit.closed_by_name}
          {permit.closeout_note && <> — {permit.closeout_note}</>}
        </p>
      )}

      <ErrorNote error={issue.error || suspend.error} />

      {mayIssue && (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {editable && (
            <button type="button" className={primaryButton}
                    disabled={permit.blockers.length > 0 || issue.isPending}
                    onClick={() => { if (!issue.isPending) issue.mutate() }}>
              {issue.isPending ? 'Issuing…' : 'Issue the permit'}
            </button>
          )}
          {permit.status === 'issued' && (
            <>
              <button type="button" className={secondaryButton}
                      disabled={suspend.isPending}
                      onClick={() => {
                        const reason = window.prompt('Why is the work stopping?')
                        if (reason && !suspend.isPending) suspend.mutate(reason)
                      }}>
                Stop the work
              </button>
              <button type="button" className={primaryButton}
                      onClick={() => setClosing(true)}>
                Close it out
              </button>
            </>
          )}
          {(permit.status === 'expired' || permit.status === 'suspended') && !permit.closed_at && (
            <button type="button" className={primaryButton} onClick={() => setClosing(true)}>
              Close it out
            </button>
          )}
        </div>
      )}

      {closing && (
        <CloseDialog permit={permit} onClose={() => setClosing(false)} onDone={invalidate} />
      )}
    </div>
  )
}

function PrecautionList(
  { permit, type, editable, onSaved }:
  { permit: Permit; type: PermitType; editable: boolean; onSaved: () => void },
) {
  const [draft, setDraft] = useState<Record<string, boolean>>(permit.precautions_confirmed ?? {})
  const [dirty, setDirty] = useState(false)

  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/permits/${permit.id}`,
                                { precautions_confirmed: draft }),
    onSuccess: () => { setDirty(false); onSaved() },
  })

  return (
    <>
      <SectionTitle className="mb-1.5">Before work starts</SectionTitle>
      <ul className="flex flex-col gap-1">
        {type.precautions.map((p) => (
          <li key={p.key}>
            <label className={
              'flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm ' +
              (editable ? 'cursor-pointer text-neutral-800 hover:bg-neutral-50'
                        : 'text-neutral-600')
            }>
              <input type="checkbox" className="mt-1" disabled={!editable}
                     checked={Boolean(draft[p.key])}
                     onChange={(e) => {
                       setDraft((d) => ({ ...d, [p.key]: e.target.checked }))
                       setDirty(true)
                     }} />
              <span className="min-w-0 flex-1">{p.label}</span>
            </label>
          </li>
        ))}
      </ul>
      <ErrorNote error={save.error} />
      {editable && dirty && (
        <div className="mt-2 flex justify-end">
          <button type="button" className={secondaryButton} disabled={save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            Save what has been confirmed
          </button>
        </div>
      )}
    </>
  )
}

function GasPanel(
  { permit, readings, onSaved }:
  { permit: Permit; readings: GasReading[]; onSaved: () => void },
) {
  const [adding, setAdding] = useState(false)
  const [o2, setO2] = useState('')
  const [lel, setLel] = useState('')
  const [h2s, setH2s] = useState('')
  const [co, setCo] = useState('')
  const [instrument, setInstrument] = useState('')

  const record = useMutation({
    mutationFn: () => api.post(`/api/v1/permits/${permit.id}/gas`, {
      oxygen_pct: o2 === '' ? null : Number(o2),
      lel_pct: lel === '' ? null : Number(lel),
      h2s_ppm: h2s === '' ? null : Number(h2s),
      co_ppm: co === '' ? null : Number(co),
      instrument: instrument || null,
    }),
    onSuccess: () => {
      setAdding(false); setO2(''); setLel(''); setH2s(''); setCo('')
      onSaved()
    },
  })

  const num = (v: string | null) => (v === null ? '—' : Number(v).toString())

  return (
    <div className="mt-4">
      <SectionTitle className="mb-1.5"><Gauge className="h-3.5 w-3.5" aria-hidden />
        Atmosphere</SectionTitle>

      {readings.length === 0 ? (
        <p className="text-sm text-neutral-500">No test recorded.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {readings.map((r) => (
            <li key={r.id}>
              <div className={
                'rounded-lg border px-3 py-2 ' +
                (r.is_acceptable ? 'border-neutral-200' : 'border-danger-300 bg-danger-50')
              }>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-sm tabular-nums">
                  <span>O₂ {num(r.oxygen_pct)}%</span>
                  <span>LEL {num(r.lel_pct)}%</span>
                  <span>H₂S {num(r.h2s_ppm)}</span>
                  <span>CO {num(r.co_ppm)}</span>
                  {r.is_acceptable
                    ? <span className="flex items-center gap-1 font-sans text-xs font-semibold text-success-700">
                        <Check className="h-3.5 w-3.5" aria-hidden />
                        Acceptable
                      </span>
                    : <span className="font-sans text-xs font-semibold text-danger-700">
                        Not acceptable
                      </span>}
                  {r.is_acceptable && !r.is_fresh && (
                    <span className="font-sans text-xs font-semibold text-warning-700">
                      Too old to rely on — retest
                    </span>
                  )}
                </div>
                {r.failed.length > 0 && (
                  <p className="mt-1 text-xs text-danger-700">{r.failed.join('; ')}</p>
                )}
                <p className="mt-0.5 text-xs text-neutral-500">
                  {formatDateTime(r.taken_at)}
                  {r.taken_by_name && ` · ${r.taken_by_name}`}
                  {r.instrument && ` · ${r.instrument}`}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}

      {permit.status !== 'closed' && (
        adding ? (
          <div className="mt-2 rounded-lg border border-neutral-200 p-2.5">
            <div className="grid gap-2 sm:grid-cols-4">
              <Field label="O₂ %">
                <input className={inputClass} inputMode="decimal" value={o2}
                       onChange={(e) => setO2(e.target.value)} placeholder="20.9" />
              </Field>
              <Field label="LEL %">
                <input className={inputClass} inputMode="decimal" value={lel}
                       onChange={(e) => setLel(e.target.value)} placeholder="0" />
              </Field>
              <Field label="H₂S ppm">
                <input className={inputClass} inputMode="decimal" value={h2s}
                       onChange={(e) => setH2s(e.target.value)} />
              </Field>
              <Field label="CO ppm">
                <input className={inputClass} inputMode="decimal" value={co}
                       onChange={(e) => setCo(e.target.value)} />
              </Field>
            </div>
            <div className="mt-2">
              <Field label="Instrument">
                <input className={inputClass} value={instrument}
                       onChange={(e) => setInstrument(e.target.value)}
                       placeholder="MSA Altair 4XR" />
              </Field>
            </div>
            <p className="mt-2 text-xs text-neutral-500">
              Leave a gas blank if it was not measured. A blank oxygen reading
              fails — &ldquo;we did not measure it&rdquo; and &ldquo;it was fine&rdquo;
              must not look the same.
            </p>
            <ErrorNote error={record.error} />
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className={secondaryButton}
                      onClick={() => setAdding(false)}>Cancel</button>
              <button type="button" className={primaryButton} disabled={record.isPending}
                      onClick={() => { if (!record.isPending) record.mutate() }}>
                {record.isPending ? 'Recording…' : 'Record the test'}
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className={`${secondaryButton} mt-2`}
                  onClick={() => setAdding(true)}>
            <Gauge className="h-4 w-4" aria-hidden />
            Record a test
          </button>
        )
      )}
    </div>
  )
}

function SignaturePanel(
  { permit, type, signatures, editable, onSaved }: {
    permit: Permit; type: PermitType | undefined
    signatures: Signature[]; editable: boolean; onSaved: () => void
  },
) {
  const profile = useMyProfile()
  const [capacity, setCapacity] = useState('')
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [signature, setSignature] = useState<string | null>(null)

  const sign = useMutation({
    mutationFn: () => api.post(`/api/v1/permits/${permit.id}/signatures`, {
      capacity,
      user_id: person.userId,
      person_name: person.name,
      external_company: person.externalCompany,
      signature,
    }),
    onSuccess: () => {
      setCapacity(''); setPerson(EMPTY_PERSON); setSignature(null)
      onSaved()
    },
  })

  const issueSigs = signatures.filter((s) => s.phase === 'issue')
  const needed = (type?.required_signatures ?? []).filter(
    (c) => !issueSigs.some((s) => s.capacity === c))

  return (
    <div className="mt-4">
      {permit.qr_token && (
        <div className="mb-4 border-b border-neutral-100 pb-3.5">
          <QrPanel
            token={permit.qr_token}
            caption="Scan at the job for this permit"
            sub={`${permit.permit_no}${permit.location_path ? ` · ${permit.location_path}` : ''}`}
          />
        </div>
      )}
      <SectionTitle className="mb-1.5">Signatures</SectionTitle>
      {signatures.length === 0 ? (
        <p className="text-sm text-neutral-500">Nobody has signed yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {signatures.map((s) => (
            <li key={s.id}
                className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
              <span className="font-semibold">{CAPACITY_LABELS[s.capacity] ?? s.capacity}</span>
              {' · '}{s.person_name}
              {s.phase === 'close' && <span className="ml-1 text-neutral-400">at close</span>}
            </li>
          ))}
        </ul>
      )}

      {editable && needed.length > 0 && (
        <div className="mt-2 rounded-lg border border-neutral-200 p-2.5">
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Signing as">
              <select className={inputClass} value={capacity}
                      onChange={(e) => setCapacity(e.target.value)}>
                <option value="">Select…</option>
                {needed.map((c) => (
                  <option key={c} value={c}>{CAPACITY_LABELS[c] ?? c}</option>
                ))}
              </select>
            </Field>
            <Field label="Who">
              <PersonPicker value={person} onChange={setPerson} />
            </Field>
          </div>
          <div className="mt-2">
            <SignaturePad value={signature} onChange={setSignature}
                          savedSignature={profile.data?.signature_image ?? null} />
          </div>
          <ErrorNote error={sign.error} />
          <div className="mt-2 flex justify-end">
            <button type="button" className={secondaryButton}
                    disabled={!capacity || !person.name.trim() || !signature || sign.isPending}
                    onClick={() => { if (!sign.isPending) sign.mutate() }}>
              Add the signature
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function CloseDialog(
  { permit, onClose, onDone }: { permit: Permit; onClose: () => void; onDone: () => void },
) {
  const profile = useMyProfile()
  const [note, setNote] = useState('')
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [signature, setSignature] = useState<string | null>(null)

  const close = useMutation({
    mutationFn: () => api.post(`/api/v1/permits/${permit.id}/close`, {
      closeout_note: note,
      signature: {
        capacity: 'issuer', user_id: person.userId,
        person_name: person.name, signature,
      },
    }),
    onSuccess: () => { onDone(); onClose() },
  })

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-label="Close the permit"
           className="w-full max-w-lg rounded-xl bg-white p-4 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-neutral-900">
              Close {permit.permit_no}
            </h2>
            <p className="text-xs text-neutral-500">{permit.type_label}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
            <X className="h-4 w-4" />
          </button>
        </div>

        <Field label="What state the area was left in">
          <textarea className={`${inputClass} min-h-[80px] py-2`} value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Space empty, blinds removed, guards refitted, area handed back." />
        </Field>

        <div className="mt-3">
          <Field label="Signed by"
                 hint="This attests that everybody is out and the area is safe">
            <PersonPicker value={person} onChange={setPerson} />
          </Field>
        </div>
        <div className="mt-2">
          <SignaturePad value={signature} onChange={setSignature}
                        savedSignature={profile.data?.signature_image ?? null} />
        </div>

        <ErrorNote error={close.error} />

        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButton}
                  disabled={!note.trim() || !person.name.trim() || !signature || close.isPending}
                  onClick={() => { if (!close.isPending) close.mutate() }}>
            <ClipboardCheck className="h-4 w-4" aria-hidden />
            {close.isPending ? 'Closing…' : 'Close the permit'}
          </button>
        </div>
      </div>
    </div>
  )
}

function NewPermitForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [typeId, setTypeId] = useState('')
  const [description, setDescription] = useState('')
  const [locationId, setLocationId] = useState<string | null>(null)
  const [contractor, setContractor] = useState('')
  const [from, setFrom] = useState(() => new Date().toISOString().slice(0, 16))
  const [to, setTo] = useState('')

  const types = useQuery({
    queryKey: ['permit-types'],
    queryFn: () => api.get<PermitType[]>('/api/v1/permit-types'),
  })
  const type = types.data?.find((t) => t.id === typeId)

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/permits', {
      type_id: typeId,
      work_description: description,
      location_id: locationId,
      contractor_company: contractor || null,
      valid_from: from ? new Date(from).toISOString() : null,
      valid_to: to ? new Date(to).toISOString() : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permits'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Raise a permit</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Kind of work">
          <select className={inputClass} value={typeId}
                  onChange={(e) => setTypeId(e.target.value)}>
            <option value="">Select…</option>
            {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
        <Field label="Where">
          <LocationSelect value={locationId} onChange={(id) => setLocationId(id)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="What is being done">
            <textarea className={`${inputClass} min-h-[60px] py-2`} value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Internal inspection of the evaporator calandria" />
          </Field>
        </div>
        <Field label="From">
          <input type="datetime-local" className={inputClass} value={from}
                 onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="Until"
               hint={type
                 ? `${type.name} may not run longer than ${type.max_duration_hours} hours`
                 : 'A permit that never expires is not a permit'}>
          <input type="datetime-local" className={inputClass} value={to}
                 onChange={(e) => setTo(e.target.value)} />
        </Field>
        <Field label="Contractor" hint="Leave blank for our own people">
          <input className={inputClass} value={contractor}
                 onChange={(e) => setContractor(e.target.value)} />
        </Field>
      </div>

      {type && (
        <p className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          {type.name} needs {type.precautions.length} precautions confirmed
          {type.requires_gas_test && ', an atmospheric test'}
          {' and signatures from the '}
          {type.required_signatures.map((c) => CAPACITY_LABELS[c] ?? c).join(', ')}.
        </p>
      )}

      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!typeId || !description.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Raising…' : 'Raise it'}
        </button>
      </div>
    </Card>
  )
}
