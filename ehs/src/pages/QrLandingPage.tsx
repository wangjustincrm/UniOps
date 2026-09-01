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
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { ClipboardCheck, FileText, MapPin, TriangleAlert, Wrench } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Card, primaryButton, secondaryButton } from '@/components/ui'

interface QrTarget {
  kind: string
  id: string
  label: string
  path: string
  actions: string[]
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
  // reach it defeats the point of putting a sticker on the wall.
  const auto = target.data?.kind === 'location'
  useEffect(() => {
    if (auto && target.data) navigate(target.data.path, { replace: true })
  }, [auto, target.data, navigate])

  if (target.isLoading) {
    return <p className="p-6 text-center text-sm text-neutral-500">Reading the code…</p>
  }

  if (target.error) {
    const message = target.error instanceof ApiError && target.error.status === 404
      ? (target.error.body as { detail?: string })?.detail ?? 'This code is not in use.'
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
        {t.actions.includes('record_inspection') && (
          <button type="button" className={secondaryButton} onClick={() => navigate('/equipment')}>
            <ClipboardCheck className="h-4 w-4" aria-hidden />
            Record a check
          </button>
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
