/**
 * ★ UAT 13: the Food Safety Risk Assessment, on a food-safety near miss only.
 *
 * Kirk: "allow the other 'assess the risk' button and related form to remain on
 * the near miss form, add this new one just under it and title it 'Food Safety
 * Risk Assessment'. Display this new risk matrix in similar way the 'assess the
 * risk' is displayed once it is completed." Probability × Severity ×
 * Detectability = the Risk Priority Number; a Critical Control Point failure
 * sets it to 46. The scales and the bands are the server's (one copy of HSE's
 * wording), and so is the arithmetic of what is saved — the form only previews.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { SectionTitle } from '@/components/ui'

interface Scale { value: number; text: string }
interface Band { code: string; floor: number; label: string; action: string }
interface Scales {
  probability: Scale[]; severity: Scale[]; detectability: Scale[]; bands: Band[]; ccp_rpn: number
}
export interface FoodSafetyAssessment {
  id: string; ccp_failure: boolean; probability: number; severity: number; detectability: number
  rpn: number; band: string; notes: string | null; assessed_by_name: string | null; assessed_at: string
}

const BAND_CLS: Record<string, string> = {
  critical: 'bg-danger-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-warning-100 text-warning-800',
  low: 'bg-success-50 text-success-700',
}

export function useFoodSafetyScales() {
  return useQuery({
    queryKey: ['food-safety-scales'],
    queryFn: () => api.get<Scales>('/api/v1/food-safety/scales'),
    staleTime: 60 * 60_000,
  })
}

export function BandBadge({ band, bands }: { band: string; bands?: Band[] }) {
  const b = bands?.find((x) => x.code === band)
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${BAND_CLS[band] ?? 'bg-neutral-100'}`}>
      {b?.label ?? band}
    </span>
  )
}

function bandFor(rpn: number, bands: Band[]): Band | undefined {
  return [...bands].sort((a, b) => b.floor - a.floor).find((b) => rpn >= b.floor)
}

export function useFoodSafetyAssessments(incidentId: string, enabled = true) {
  return useQuery({
    queryKey: ['food-safety-assessments', incidentId],
    enabled: enabled && Boolean(incidentId),
    queryFn: () => api.get<FoodSafetyAssessment[]>(`/api/v1/incidents/${incidentId}/food-safety-assessments`),
  })
}

/** The green button, as both assessments have it (UAT 14). */
export const assessButton =
  'flex min-h-[40px] w-full items-center justify-center rounded-lg bg-success-600 px-4 text-sm font-semibold text-white hover:bg-success-700'

/** ★ UAT 17: "Make the 'Start the investigation' and the 'edit the
 * investigation' buttons dark blue" — the risk button's size, in dark blue. */
export const investigateButton =
  'flex min-h-[40px] w-full items-center justify-center rounded-lg bg-info-800 px-4 text-sm font-semibold text-white hover:bg-info-900'

/**
 * ★ UAT 14: both assess buttons green, on one line — the page sets this
 * beside the risk assessment. ★ UAT 15: "place the information under the
 * button", so the button heads this section and what was assessed is under it.
 */
export function FoodSafetyRisk({ incidentId, mayAssess }: { incidentId: string; mayAssess: boolean }) {
  const [assessing, setAssessing] = useState(false)
  const scales = useFoodSafetyScales()
  const list = useFoodSafetyAssessments(incidentId)
  const rows = list.data ?? []
  const latest = rows[0]
  const bands = scales.data?.bands ?? []
  const latestBand = latest ? bands.find((b) => b.code === latest.band) : undefined

  return (
    <section aria-label="Food Safety Risk Assessment">
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <SectionTitle className="mb-0">Food Safety Risk Assessment</SectionTitle>
        {latest && <BandBadge band={latest.band} bands={bands} />}
      </div>
      {mayAssess && (
        <button type="button" onClick={() => setAssessing(true)} className={`${assessButton} mb-2.5`}>
          {latest ? 'Assess the food safety risk again' : 'Assess the food safety risk'}
        </button>
      )}
      {latest && (
        <div className="mb-2.5 rounded-lg border border-neutral-200 bg-white p-3">
          {latest.ccp_failure && (
            <p className="mb-1.5 text-xs font-semibold text-danger-700">Critical Control Point failure</p>
          )}
          <dl className="grid grid-cols-4 gap-2 text-center">
            {([['Probability', latest.probability], ['Severity', latest.severity],
               ['Detectability', latest.detectability], ['RPN', latest.rpn]] as const).map(([k, v]) => (
              <div key={k} className="rounded-md bg-neutral-50 py-1.5">
                <dt className="text-[11px] uppercase tracking-wide text-neutral-500">{k}</dt>
                <dd className="text-lg font-semibold tabular-nums text-neutral-900">{v}</dd>
              </div>
            ))}
          </dl>
          {latestBand && (
            <p className="mt-2 text-sm font-medium text-neutral-800">{latestBand.label}: {latestBand.action}</p>
          )}
          {latest.notes && <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-700">{latest.notes}</p>}
          <p className="mt-1 text-xs text-neutral-500">
            {latest.assessed_by_name ?? 'Someone'} · {formatDateTime(latest.assessed_at)}
          </p>
        </div>
      )}
      {!latest && !list.isPending && (
        <p className="text-sm text-neutral-500">Not assessed yet.</p>
      )}
      {rows.length > 1 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-semibold text-neutral-600">Earlier assessments</summary>
          <ul className="mt-1.5 flex flex-col gap-1">
            {rows.slice(1).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 text-xs text-neutral-600">
                <BandBadge band={r.band} bands={bands} />
                <span className="tabular-nums">RPN {r.rpn} = P{r.probability} × S{r.severity} × D{r.detectability}{r.ccp_failure ? ' · CCP failure' : ''}</span>
                <span>· {r.assessed_by_name ?? 'Someone'} · {formatDateTime(r.assessed_at)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {assessing && scales.data && (
        <AssessDialog incidentId={incidentId} scales={scales.data} onClose={() => setAssessing(false)} />
      )}
    </section>
  )
}

function ScalePick({ title, scale, value, onChange }: {
  title: string; scale: Scale[]; value: number | null; onChange: (n: number) => void
}) {
  return (
    <fieldset className="mt-3">
      <legend className="mb-1 text-sm font-semibold text-neutral-800">{title}</legend>
      <div className="flex flex-col gap-1">
        {scale.map((s) => (
          <label key={s.value}
                 className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-1.5 text-sm ${
                   value === s.value ? 'border-primary-400 bg-primary-50' : 'border-neutral-200 bg-white'}`}>
            <input type="radio" name={title} className="mt-1" checked={value === s.value}
                   onChange={() => onChange(s.value)} />
            <span><b className="tabular-nums">{s.value}</b> — {s.text}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function AssessDialog({ incidentId, scales, onClose }: {
  incidentId: string; scales: Scales; onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [ccp, setCcp] = useState(false)
  const [p, setP] = useState<number | null>(null)
  const [s, setS] = useState<number | null>(null)
  const [d, setD] = useState<number | null>(null)
  const [notes, setNotes] = useState('')
  const ready = p !== null && s !== null && d !== null
  const preview = ccp ? scales.ccp_rpn : ready ? p! * s! * d! : null
  const band = preview !== null ? bandFor(preview, scales.bands) : undefined
  const save = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${incidentId}/food-safety-assessments`, {
      ccp_failure: ccp, probability: p, severity: s, detectability: d, notes: notes.trim() || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['food-safety-assessments', incidentId] })
      void queryClient.invalidateQueries({ queryKey: ['food-safety-register'] })
      onClose()
    },
  })
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div role="dialog" aria-label="Food Safety Risk Assessment"
           className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-t-2xl bg-white p-4 sm:rounded-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-900">Food Safety Risk Assessment</h2>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-50"><X className="h-4 w-4" /></button>
        </div>
        {/* ★ "Put a checkmark question at the top … asking if this was a
            Critical Control Point failure." */}
        <label className="mt-3 flex items-start gap-2 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-800">
          <input type="checkbox" className="mt-0.5 h-4 w-4" checked={ccp} onChange={(e) => setCcp(e.target.checked)} />
          <span><b>Was this a Critical Control Point failure?</b> Ticked, the RPN is {scales.ccp_rpn}, whatever the ratings.</span>
        </label>
        <ScalePick title="Probability" scale={scales.probability} value={p} onChange={setP} />
        <ScalePick title="Severity" scale={scales.severity} value={s} onChange={setS} />
        <ScalePick title="Detectability" scale={scales.detectability} value={d} onChange={setD} />
        <label className="mt-3 block text-sm font-semibold text-neutral-800">
          Notes
          <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-normal" />
        </label>
        <div className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-sm">
          {preview === null ? 'Rate all three to see the Risk Priority Number.' : (
            <>
              <b className="tabular-nums">RPN {preview}</b>
              {!ccp && ready && <span className="text-neutral-500"> = {s} × {p} × {d}</span>}
              {band && <> · <b>{band.label}</b>: {band.action}</>}
            </>
          )}
        </div>
        {save.isError && <p className="mt-2 text-xs text-danger-700">{(save.error as ApiError).message}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[40px] rounded-lg border border-neutral-300 px-3 text-sm">Cancel</button>
          <button type="button" disabled={!ready || save.isPending}
                  onClick={() => { if (ready && !save.isPending) save.mutate() }}
                  className="min-h-[40px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50">
            {save.isPending ? 'Saving…' : 'Save the assessment'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
