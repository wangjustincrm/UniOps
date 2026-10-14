/**
 * Where a scanned code lands.
 *
 * One screen for every kind of printed code, because the person holding the
 * phone does not know — and should not have to know — whether they are pointing
 * it at an area, a piece of equipment or a policy. The server says what the
 * token is; this offers what can be done from there and gets out of the way.
 *
 * A code that resolves to exactly one sensible action goes straight there. An
 * area code is the case that matters: somebody scanning the sticker by a door
 * because they need to report something should land on the report form with the
 * area already filled in, not on a menu.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Check, ClipboardCheck, FileText, MapPin, TriangleAlert, Wrench } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Card, ErrorNote, primaryButton, secondaryButton } from '@/components/ui'
import { formatDate } from '@/lib/formatDate'
import { SignaturePad } from '@/components/SignaturePad'

interface QrTarget {
  kind: string
  id: string
  label: string
  path: string
  actions: string[]
  /** When this person already signed this version of a document. */
  signed_at: string | null
  /** A quiz the document asks for before it is signed, and whether it is passed. */
  quiz_id?: string | null
  quiz_passed?: boolean | null
}

const ICONS: Record<string, typeof MapPin> = {
  location: MapPin,
  safety_asset: Wrench,
  document: FileText,
}

const KIND_LABELS: Record<string, string> = {
  location: 'Plant area',
  safety_asset: 'Safety equipment',
  document: 'Document',
}

export default function QrLandingPage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()

  const target = useQuery({
    queryKey: ['qr', token],
    queryFn: () => api.get<QrTarget>(`/api/v1/qr/${encodeURIComponent(token)}`),
    enabled: Boolean(token),
    retry: false,
  })

  // An area code has one obvious purpose. Sending someone through a menu to
  // reach it defeats the point of putting a sticker on the wall. So does a quiz
  // code — round 5, box 4: "scan the code and get the test" — and a button
  // reading "Open the record" in front of it was one more thing to work out
  // standing up at the end of a session.
  const auto = target.data?.kind === 'location' || target.data?.kind === 'quiz'
  useEffect(() => {
    if (auto && target.data) navigate(target.data.path, { replace: true })
  }, [auto, target.data, navigate])

  if (target.isLoading) {
    return <p className="p-6 text-center text-sm text-neutral-500">Reading the code…</p>
  }

  if (target.error) {
    const message = target.error instanceof ApiError && target.error.status === 404
      ? target.error.message || 'This code is not in use.'
      : 'That code could not be read. Try again, or find the item in Safety directly.'
    return (
      <div className="p-4">
        <Card className="!border-warning-500 !bg-warning-50">
          <h1 className="text-sm font-semibold text-warning-700">Nothing behind this code</h1>
          <p className="mt-1 text-sm text-warning-700">{message}</p>
        </Card>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={primaryButton} onClick={() => navigate('/report')}>
            <TriangleAlert className="h-4 w-4" aria-hidden />
            Report an incident anyway
          </button>
          <button type="button" className={secondaryButton} onClick={() => navigate('/actions/mine')}>
            Go to Safety
          </button>
        </div>
      </div>
    )
  }

  const t = target.data
  if (!t || auto) return null
  const Icon = ICONS[t.kind] ?? MapPin

  return (
    <div className="p-4">
      <Card>
        <div className="flex items-start gap-3">
          <Icon className="mt-0.5 h-6 w-6 shrink-0 text-primary-600" aria-hidden />
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wider text-neutral-400">
              {KIND_LABELS[t.kind] ?? t.kind}
            </p>
            <h1 className="text-base font-bold text-neutral-900">{t.label}</h1>
          </div>
        </div>
      </Card>

      <div className="mt-3 flex flex-col gap-2">
        <button type="button" className={primaryButton} onClick={() => navigate(t.path)}>
          {t.kind === 'document' ? <FileText className="h-4 w-4" aria-hidden />
                                 : <Wrench className="h-4 w-4" aria-hidden />}
          {t.kind === 'document' ? 'Open it' : 'Open the record'}
        </button>
        {/* ★ Straight into this item's own inspection form, with the item's
            name on it. Round 4: "when a user scans the eye wash station qr code
            the eye wash inspection form shall open with the name of the station
            on the form automatic." An unfinished form for this item is reopened
            rather than a second one started — two half-filled checks of one
            eyewash is the same fault as two sets of minutes for one meeting. */}
        {t.actions.includes('inspection_form') && (
          <OpenInspectionForm assetId={t.id} label={t.label} />
        )}
        {t.actions.includes('record_inspection') && (
          // ★ Straight to a check on *this* item. It used to open the register
          // — every asset in the plant — which is not recording a check on the
          // thing the camera was just pointed at.
          <button type="button" className={secondaryButton}
                  onClick={() => navigate(
                    `${t.path}${t.path.includes('?') ? '&' : '?'}check=1`)}>
            <ClipboardCheck className="h-4 w-4" aria-hidden />
            Record a check
          </button>
        )}
        {/* ★ Signed here, on the screen the camera landed on. The code on a
            policy by the door exists so somebody can read it and say they
            have; sending them to another page to do that is the step where it
            stops happening. */}
        {/* ★ Round-6 review: where the document carries a quiz, signing waits
            for a pass — so the step offered here is the quiz, not a button the
            server would refuse. */}
        {t.actions.includes('acknowledge') && t.quiz_id && !t.quiz_passed && (
          <div className="rounded-xl border border-neutral-200 bg-white p-3.5">
            <p className="text-sm font-semibold text-neutral-900">Take the quiz, then sign</p>
            <p className="mt-0.5 text-xs text-neutral-500">
              This document is signed once its quiz is passed. The quiz takes you back
              to the document to sign.
            </p>
            <button type="button" className={`${primaryButton} mt-3 w-full`}
                    onClick={() => navigate(`/quizzes/${t.quiz_id}/take`)}>
              <ClipboardCheck className="h-4 w-4" aria-hidden />
              Take the quiz
            </button>
          </div>
        )}
        {t.actions.includes('acknowledge') && !(t.quiz_id && !t.quiz_passed) && (
          <AcknowledgeHere documentId={t.id} label={t.label} />
        )}
        {t.signed_at && (
          <p className="rounded-xl border border-success-500/40 bg-success-50 px-3.5 py-3 text-sm text-success-800">
            You signed this version on {formatDate(t.signed_at)}. Nothing else is
            needed.
          </p>
        )}
        {t.actions.includes('report_incident') && (
          <button type="button" className={secondaryButton} onClick={() => navigate('/report')}>
            <TriangleAlert className="h-4 w-4" aria-hidden />
            Report an incident
          </button>
        )}
      </div>
    </div>
  )
}


/**
 * Reading and signing a policy from the code on the wall.
 *
 * The signature is optional, as it is everywhere else here: the record of who
 * acknowledged what and when is the thing Read & Sign is for, and refusing to
 * take that without a drawn squiggle on a phone in a corridor would lose more
 * records than it secures.
 */
function AcknowledgeHere({ documentId, label }: { documentId: string; label: string }) {
  const [signature, setSignature] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sign = useMutation({
    mutationFn: () => api.post(`/api/v1/documents/${documentId}/acknowledge`, { signature }),
    onSuccess: () => setDone(true),
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  if (done) {
    return (
      <div className="rounded-xl border border-success-500/40 bg-success-50 p-3.5">
        <p className="text-sm font-semibold text-success-800">Signed.</p>
        <p className="mt-0.5 text-xs text-success-700">
          Your name and the time are on the record for {label}.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-3.5">
      <p className="text-sm font-semibold text-neutral-900">Sign to say you have read it</p>
      <p className="mt-0.5 text-xs text-neutral-500">
        A signature is optional — pressing the button is the record.
      </p>
      <div className="mt-2">
        <SignaturePad value={signature} onChange={setSignature} />
      </div>
      {error && (
        <p className="mt-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">{error}</p>
      )}
      <button type="button" className={`${primaryButton} mt-3 w-full`}
              disabled={sign.isPending}
              onClick={() => { if (!sign.isPending) { setError(null); sign.mutate() } }}>
        <Check className="h-4 w-4" aria-hidden />
        {sign.isPending ? 'Signing…' : 'I have read this'}
      </button>
    </div>
  )
}


/**
 * Opening the form a piece of equipment is checked on.
 *
 * Its own component because it makes a request: the submission is created (or
 * the unfinished one found) by the server, which is the only place that knows
 * which template the item is linked to.
 */
function OpenInspectionForm({ assetId, label }: { assetId: string; label: string }) {
  const navigate = useNavigate()
  const open = useMutation({
    mutationFn: () => api.post<{ submission_id: string; submission_no: string }>(
      `/api/v1/safety-assets/${assetId}/inspection-form/start`, {}),
    onSuccess: (r) => navigate(`/forms/submissions/${r.submission_id}`),
  })
  return (
    <>
      <button type="button" className={primaryButton} disabled={open.isPending}
              onClick={() => { if (!open.isPending) open.mutate() }}>
        <ClipboardCheck className="h-4 w-4" aria-hidden />
        {open.isPending ? 'Opening…' : `Inspect ${label.split(' · ').slice(-1)[0]}`}
      </button>
      <ErrorNote error={open.error} />
    </>
  )
}
