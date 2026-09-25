/**
 * One document: read it, sign it, and — if you look after it — publish it.
 *
 * The PDF is rendered with pdfjs onto a canvas rather than in an iframe. The
 * browser's native PDF viewer is blocked by company policy here, so an iframe
 * shows an empty box and the person cannot read what they are being asked to
 * sign. Same approach as the OA module's preview.
 *
 * Signing is a drawn signature rather than a checkbox because HSE asked for one
 * on the documents that matter, and because "I ticked a box" and "here is my
 * signature" are different things to hold up in an investigation.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { Check, Download, Upload, Users } from 'lucide-react'
import { api, apiBlob } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { useMyProfile } from '@/hooks/useMyProfile'
import { usePermissions } from '@/hooks/usePermissions'
import { SignaturePad } from '@/components/SignaturePad'
import { ChangeHistory } from '@/components/ChangeHistory'
import {
  Card, Empty, ErrorNote, PageHeader, primaryButton, secondaryButton, LoadProblem, LoadFailedNote,
} from '@/components/ui'
import { CATEGORY_LABELS, type SafetyDoc } from '@/pages/DocumentsPage'
import QrPanel from '@/components/QrPanel'

interface Ack {
  id: string; user_id: string | null; user_name: string; user_role: string | null
  version: string; acknowledged_at: string
}
interface SigningStatus {
  version: string; audience: number; signed: number; percent: number
  outstanding: { user_id: string; user_name: string }[]
}

export default function DocumentDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const mayManage = can('ehs.document.manage')

  const doc = useQuery({
    queryKey: ['document', id],
    queryFn: () => api.get<SafetyDoc>(`/api/v1/documents/${id}`),
    enabled: Boolean(id),
  })
  const mine = useQuery({
    queryKey: ['documents', 'mine'],
    queryFn: () => api.get<SafetyDoc[]>('/api/v1/documents/mine'),
  })

  useTabTitle(ehsRoutes, doc.data?.doc_no)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['document', id] })
    queryClient.invalidateQueries({ queryKey: ['documents'] })
    queryClient.invalidateQueries({ queryKey: ['document-signing', id] })
    queryClient.invalidateQueries({ queryKey: ['document-acks', id] })
  }

  if (doc.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ The reason first. A refused or failed request leaves `data`
  // undefined exactly as a deleted record does, so this used to answer
  // "not found" to a 403 and to a server error alike.
  if (doc.isError) return <LoadProblem error={doc.error} noun="document" />
  if (!doc.data) return <LoadProblem error={null} noun="document" />
  const d = doc.data
  const iOweASignature = (mine.data ?? []).some((m) => m.id === d.id)

  return (
    <div className="p-4">
      <PageHeader
        title={d.title}
        subtitle={`${d.doc_no} · ${CATEGORY_LABELS[d.category] ?? d.category} · v${d.version}`}
      >
        {d.file_id && <DownloadButton doc={d} />}
      </PageHeader>


      {d.qr_token && (
        <Card className="mb-4">
          <QrPanel
            token={d.qr_token}
            caption="Scan to open this document"
            sub={`${d.doc_no} · v${d.version}`}
          />
        </Card>
      )}
      <Card className="mb-4">
        {d.summary && <p className="mb-3 text-sm text-neutral-700">{d.summary}</p>}
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <Detail label="Status">
            {d.status === 'published' ? 'Published' :
             d.status === 'retired' ? 'Superseded by a later version' : 'Draft'}
            {d.published_at && ` · ${formatDate(d.published_at)}`}
          </Detail>
          <Detail label="Effective">{d.effective_on ? formatDate(d.effective_on) : '—'}</Detail>
          <Detail label="Next review">
            {d.review_due_on
              ? `${formatDate(d.review_due_on)}${d.category === 'policy' ? ' — OHSA s.25(2)(j)' : ''}`
              : 'Not on a review cycle'}
          </Detail>
          <Detail label="Owner">{d.owner_name ?? '—'}</Detail>
        </dl>
      </Card>

      {iOweASignature && <SignPanel doc={d} onSigned={invalidate} />}

      {mayManage && d.status === 'draft' && <DraftControls doc={d} onChanged={invalidate} />}

      {d.requires_ack && d.status !== 'draft' && <SigningPanel doc={d} />}

      {mayManage && d.status === 'published' && <PublishedControls doc={d} onChanged={invalidate} />}

      <ChangeHistory table="ehs_documents" rowId={id} />
    </div>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="text-sm text-neutral-900">{children}</dd>
</div>
  )
}

function DownloadButton({ doc }: { doc: SafetyDoc }) {
  const [busy, setBusy] = useState(false)
  // The file is proxied by ehs-api so the request carries a bearer token,
  // which a plain <a href> cannot send.
  const open = async () => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await apiBlob(`/api/v1/documents/${doc.id}/file`)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } finally {
      setBusy(false)
    }
  }
  return (
    <button type="button" className={secondaryButton} onClick={open} disabled={busy}>
      <Download className="h-4 w-4" aria-hidden />
      {busy ? 'Opening…' : (doc.filename ?? 'Open the document')}
    </button>
  )
}

function SignPanel({ doc, onSigned }: { doc: SafetyDoc; onSigned: () => void }) {
  const [signature, setSignature] = useState<string | null>(null)
  // The signature saved on the person's profile, offered as the default so
  // acknowledging a policy is one tap rather than a drawing exercise.
  const profile = useMyProfile()
  const sign = useMutation({
    mutationFn: () => api.post(`/api/v1/documents/${doc.id}/acknowledge`, { signature }),
    onSuccess: onSigned,
  })

  return (
    <section className="mb-4 rounded-xl border border-warning-500 bg-warning-50 p-3.5">
      <h2 className="text-sm font-semibold text-warning-700">
        You are asked to read and sign this
      </h2>
      <p className="mt-0.5 text-xs text-warning-700">
        Open the document above first. Signing records that you have read
        version {doc.version}.
      </p>
      <div className="mt-3 rounded-lg bg-white p-2">
        <SignaturePad value={signature} onChange={setSignature}
                      savedSignature={profile.data?.signature_image ?? null} />
      </div>
      <ErrorNote error={sign.error} />
      <div className="mt-3 flex justify-end">
        <button type="button" className={primaryButton}
                disabled={!signature || sign.isPending}
                onClick={() => { if (!sign.isPending) sign.mutate() }}>
          <Check className="h-4 w-4" aria-hidden />
          {sign.isPending ? 'Recording…' : 'I have read this'}
        </button>
      </div>
    </section>
  )
}

function DraftControls({ doc, onChanged }: { doc: SafetyDoc; onChanged: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.upload(`/api/v1/documents/${doc.id}/file`, form)
    },
    onSuccess: onChanged,
  })
  const publish = useMutation({
    mutationFn: () => api.post(`/api/v1/documents/${doc.id}/publish`, {}),
    onSuccess: onChanged,
  })

  return (
    <Card className="mb-4">
      <h2 className="mb-2 text-sm font-semibold text-neutral-900">Before publishing</h2>
      <ol className="flex flex-col gap-1.5 text-sm text-neutral-700">
        <li className="flex items-center gap-2">
          {doc.file_id
            ? <Check className="h-4 w-4 shrink-0 text-success-600" aria-hidden />
            : <span className="h-4 w-4 shrink-0 rounded-full border border-neutral-300" />}
          {doc.file_id ? `Attached — ${doc.filename}` : 'Attach the document'}
        </li>
        <li className="flex items-center gap-2">
          {doc.review_months
            ? <Check className="h-4 w-4 shrink-0 text-success-600" aria-hidden />
            : <span className="h-4 w-4 shrink-0 rounded-full border border-neutral-300" />}
          {doc.review_months
            ? `Reviewed every ${doc.review_months} months`
            : 'No review cycle — this document will never come up for review'}
        </li>
      </ol>

      <input ref={fileRef} type="file" className="sr-only"
             accept=".pdf,.doc,.docx,image/*"
             onChange={(e) => {
               const file = e.target.files?.[0]
               if (file) upload.mutate(file)
               e.target.value = ''
             }} />

      <ErrorNote error={upload.error || publish.error} />

      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} disabled={upload.isPending}
                onClick={() => fileRef.current?.click()}>
          <Upload className="h-4 w-4" aria-hidden />
          {upload.isPending ? 'Uploading…' : doc.file_id ? 'Replace the file' : 'Attach the file'}
        </button>
        <button type="button" className={primaryButton}
                disabled={!doc.file_id || publish.isPending}
                onClick={() => { if (!publish.isPending) publish.mutate() }}>
          {publish.isPending ? 'Publishing…' : 'Publish'}
        </button>
      </div>
      {doc.requires_ack && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          Publishing asks everyone in its audience to sign, and puts it in their inbox.
        </p>
      )}
    </Card>
  )
}

function PublishedControls({ doc, onChanged }: { doc: SafetyDoc; onChanged: () => void }) {
  const [version, setVersion] = useState('')
  const newVersion = useMutation({
    mutationFn: () => api.post<SafetyDoc>(`/api/v1/documents/${doc.id}/new-version`, { version }),
    onSuccess: (draft) => {
      onChanged()
      window.location.href = `/documents/${draft.id}`
    },
  })

  return (
    <Card className="mt-4">
      <h2 className="text-sm font-semibold text-neutral-900">Revise this document</h2>
      <p className="mt-0.5 text-xs text-neutral-500">
        A new version starts as a draft. Publishing it retires this one, and
        everybody is asked to sign again — a signature on v{doc.version} does not
        carry forward.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-neutral-700">New version</span>
          <input className="min-h-[44px] w-28 rounded-lg border border-neutral-300 px-3 text-sm"
                 value={version} onChange={(e) => setVersion(e.target.value)}
                 placeholder={nextVersion(doc.version)} />
        </label>
        <button type="button" className={secondaryButton}
                disabled={newVersion.isPending}
                onClick={() => {
                  if (newVersion.isPending) return
                  if (!version) setVersion(nextVersion(doc.version))
                  newVersion.mutate()
                }}>
          {newVersion.isPending ? 'Creating…' : 'Start a new version'}
        </button>
      </div>
      <ErrorNote error={newVersion.error} />
    </Card>
  )
}

/** "1.0" -> "2.0"; anything unparseable is left for the person to type. */
function nextVersion(current: string): string {
  const major = Number.parseInt(current, 10)
  return Number.isFinite(major) ? `${major + 1}.0` : ''
}

function SigningPanel({ doc }: { doc: SafetyDoc }) {
  const [showing, setShowing] = useState<'outstanding' | 'signed'>('outstanding')

  const status = useQuery({
    queryKey: ['document-signing', doc.id],
    queryFn: () => api.get<SigningStatus>(`/api/v1/documents/${doc.id}/signing-status`),
  })
  const acks = useQuery({
    queryKey: ['document-acks', doc.id],
    queryFn: () => api.get<Ack[]>(`/api/v1/documents/${doc.id}/acknowledgements`),
    enabled: showing === 'signed',
  })

  const s = status.data
  // ★ It used to return null here, so a failed request took the whole
  // read-and-sign section off the page — and a document with no such section
  // reads as a document nobody has to acknowledge. Saying so is the point.
  if (status.isError) {
    return (
      <Card className="mt-4">
        <LoadFailedNote what="Who has signed this" />
      </Card>
    )
  }
  if (!s) return null

  return (
    <Card className="mt-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
          <Users className="h-4 w-4 text-neutral-400" aria-hidden />
          Read &amp; sign
        </h2>
        <span className="text-sm tabular-nums text-neutral-600">
          {s.signed} of {s.audience} signed
        </span>
      </div>

      <div className="mb-3 h-2 overflow-hidden rounded-full bg-neutral-100">
        <div className="h-full rounded-full bg-primary-500"
             style={{ width: `${s.percent}%` }} aria-hidden />
      </div>

      <div className="mb-2 flex gap-1">
        <Toggle active={showing === 'outstanding'} onClick={() => setShowing('outstanding')}>
          Still to sign ({s.outstanding.length})
        </Toggle>
        <Toggle active={showing === 'signed'} onClick={() => setShowing('signed')}>
          Signed ({s.signed})
        </Toggle>
      </div>

      {showing === 'outstanding' ? (
        s.outstanding.length === 0 ? (
          <p className="rounded-lg bg-success-50 px-3 py-2 text-sm text-success-700">
            Everybody has signed version {s.version}.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {s.outstanding.map((p) => (
              <li key={p.user_id}
                  className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
                {p.user_name}
              </li>
            ))}
          </ul>
        )
      ) : (
        acks.data?.length === 0 ? (
          <Empty>Nobody has signed this version yet.</Empty>
        ) : (
          <ul className="flex flex-col gap-1">
            {acks.data?.map((a) => (
              <li key={a.id} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                <span className="font-medium text-neutral-800">{a.user_name}</span>
                <span className="text-neutral-500">{formatDateTime(a.acknowledged_at)}</span>
              </li>
            ))}
          </ul>
        )
      )}
    </Card>
  )
}

function Toggle(
  { active, onClick, children }:
  { active: boolean; onClick: () => void; children: React.ReactNode },
) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
            className={
              'min-h-[32px] rounded-lg px-2.5 text-xs font-semibold transition-colors ' +
              (active ? 'bg-neutral-800 text-white'
                      : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')
            }>
      {children}
    </button>
  )
}
