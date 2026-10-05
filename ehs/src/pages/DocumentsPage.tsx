/**
 * The safety document library, and Read & Sign.
 *
 * Everyone can open this page — that is the point of a policy library — so it
 * opens with what *this* person still has to read rather than with the whole
 * catalogue. For most people that list is the entire reason they are here.
 *
 * The management view of Read & Sign is deliberately the outstanding names, not
 * the percentage: a chaser needs to know who to chase.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { FileText, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, Tabs, LoadFailedNote,
} from '@/components/ui'

export interface SafetyDoc {
  id: string; doc_no: string; title: string; category: string; summary: string | null
  version: string; file_id: string | null; filename: string | null
  effective_on: string | null; review_due_on: string | null; review_months: number | null
  status: string; published_at: string | null; published_by_name: string | null
  supersedes_id: string | null; requires_ack: boolean; ack_roles: string[]
  qr_token: string | null; owner_name: string | null
  /** When this version has been retired by a newer one — only the single
   *  document read fills these in. */
  superseded_by_id?: string | null
  superseded_by_version?: string | null
}

export const CATEGORY_LABELS: Record<string, string> = {
  policy: 'Policy', procedure: 'Procedure', emergency: 'Emergency plan',
  sds: 'Safety data sheet', guideline: 'Guideline', form: 'Form', other: 'Other',
}

const TABS = [
  { key: 'mine', label: 'To read' },
  { key: 'library', label: 'Library' },
  { key: 'drafts', label: 'Drafts' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function DocumentsPage() {
  const { can } = usePermissions()
  const mayManage = can('ehs.document.manage')
  const [tab, setTab] = useState<TabKey>('mine')

  const mine = useQuery({
    queryKey: ['documents', 'mine'],
    queryFn: () => api.get<SafetyDoc[]>('/api/v1/documents/mine'),
  })

  const tabs = TABS
    .filter((t) => t.key !== 'drafts' || mayManage)
    .map((t) => t.key === 'mine' ? { ...t, count: mine.data?.length } : t)

  return (
    <div className="p-4">
      <PageHeader title="Safety documents" />
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {/* ★ Nothing to read is a statement an employee acts on: they conclude
          they have no policies outstanding. */}
      {mine.isError && <LoadFailedNote what="What you have to read" />}
      {tab === 'mine' && <ToReadTab docs={mine.data ?? []} loading={mine.isLoading} />}
      {tab === 'library' && <LibraryTab statuses={['published']} mayManage={mayManage} />}
      {tab === 'drafts' && <LibraryTab statuses={['draft', 'retired']} mayManage={mayManage} />}
    </div>
  )
}

function ToReadTab({ docs, loading }: { docs: SafetyDoc[]; loading: boolean }) {
  const navigate = useNavigate()
  if (loading) return <p className="text-sm text-neutral-500">Loading…</p>
  if (docs.length === 0) {
    return (
      <Empty>
        Nothing waiting for you. Anything published that you are asked to
        acknowledge appears here, and in your task inbox.
      </Empty>
    )
  }
  return (
    <ul className="flex flex-col gap-2">
      {docs.map((d) => (
        <li key={d.id}>
          <button type="button" onClick={() => navigate(`/documents/${d.id}`)}
                  className="flex w-full items-start gap-3 rounded-xl border border-warning-500 bg-warning-50 p-3.5 text-left transition-colors hover:border-warning-600">
            <FileText className="mt-0.5 h-5 w-5 shrink-0 text-warning-700" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-neutral-900">{d.title}</span>
              <span className="mt-0.5 block text-xs text-warning-700">
                v{d.version} · needs your signature
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function LibraryTab({ statuses, mayManage }: { statuses: string[]; mayManage: boolean }) {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const query = statuses.map((s) => `status=${s}`).join('&')

  const docs = useQuery({
    queryKey: ['documents', statuses],
    queryFn: () => api.get<SafetyDoc[]>(`/api/v1/documents?${query}`),
  })

  const byCategory = new Map<string, SafetyDoc[]>()
  for (const d of docs.data ?? []) {
    const list = byCategory.get(d.category) ?? []
    list.push(d)
    byCategory.set(d.category, list)
  }

  return (
    <>
      {/* ★ On both library tabs. It was drawn only where the published documents
          are listed, and what it creates is a draft — so the tab where drafts
          live was the one place you could not start one. Same shape as the
          chemical register, where the way in was behind a tab nobody lands on. */}
      {mayManage && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a document
          </button>
        </div>
      )}
      {creating && <NewDocumentForm onClose={() => setCreating(false)} />}

      {docs.data?.length === 0 && !creating ? (
        <Empty>
          {statuses.includes('published')
            ? 'Nothing published yet. A document appears here once it has a file attached and has been published.'
            : 'No drafts. A document starts here and moves to the library when it is published.'}
        </Empty>
      ) : (
        [...byCategory.entries()].map(([category, list]) => (
          <section key={category} className="mb-5">
            <SectionTitle className="mb-2">{CATEGORY_LABELS[category] ?? category}</SectionTitle>
            <ul className="flex flex-col gap-2">
              {list.map((d) => (
                <li key={d.id}>
                  <button type="button" onClick={() => navigate(`/documents/${d.id}`)}
                          className="flex w-full items-start gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300">
                    <FileText className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-neutral-900">{d.title}</span>
                      <span className="mt-0.5 block text-xs text-neutral-500">
                        {d.doc_no} · v{d.version}
                        {d.effective_on && ` · effective ${formatDate(d.effective_on)}`}
                        {d.review_due_on && ` · review due ${formatDate(d.review_due_on)}`}
                      </span>
                    </span>
                    {d.status === 'retired' && (
                      <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                        Superseded
                      </span>
                    )}
                    {d.status === 'draft' && (
                      <span className="shrink-0 rounded-full bg-warning-50 px-2 py-0.5 text-[11px] font-semibold text-warning-700">
                        Draft
                      </span>
                    )}
                    {d.requires_ack && d.status === 'published' && (
                      <span className="shrink-0 rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">
                        Read &amp; sign
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </>
  )
}

function NewDocumentForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('policy')
  const [summary, setSummary] = useState('')
  const [version, setVersion] = useState('1.0')
  const [reviewMonths, setReviewMonths] = useState('12')
  const [requiresAck, setRequiresAck] = useState(true)
  // ★ Named people, as well as roles. A procedure that concerns four people on
  // one line corresponds to no role, and sending it to whoever holds the
  // nearest one teaches thirty others to ignore the next thing they are sent.
  const [named, setNamed] = useState<PersonValue[]>([])
  const [adding, setAdding] = useState<PersonValue>(EMPTY_PERSON)

  // ★ The document itself, on the form that creates it. Round 4: "add the
  // ability to add an attachment at the start of the document creation process
  // just above where names are added." It was a second step on a second screen,
  // and publishing refuses a document with nothing to read — so the way in
  // ended at a record that could not be published and gave no hint why.
  const [file, setFile] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  // The document is created by the first call. If the upload then fails,
  // pressing Add again would create a second document — so what has already
  // succeeded is remembered and skipped on the retry. Same shape as the
  // chemical form, for the same reason.
  const createdId = useRef<string | null>(null)
  const [partial, setPartial] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: async () => {
      if (!createdId.current) {
        const doc = await api.post<SafetyDoc>('/api/v1/documents', {
          title, category, summary: summary || null, version,
          review_months: reviewMonths ? Number(reviewMonths) : null,
          requires_ack: requiresAck,
          ack_user_ids: named.map((p) => p.userId).filter(Boolean),
        })
        createdId.current = doc.id
      }
      const id = createdId.current
      if (file) {
        try {
          const form = new FormData()
          form.append('file', file)
          await api.upload(`/api/v1/documents/${id}/file`, form)
        } catch (e) {
          // The document exists, so the list has to be told — somebody sent to
          // "open it and attach the file there" needs to find it.
          queryClient.invalidateQueries({ queryKey: ['documents'] })
          setPartial('The document was added, but the file was not attached. '
                     + 'Open it and attach the file from there.')
          throw e
        }
      }
      return { id }
    },
    onSuccess: ({ id }) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      onClose()
      navigate(`/documents/${id}`)
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Add a document</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Title">
          <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                 placeholder="Health and Safety Policy" />
        </Field>
        <Field label="Kind">
          <select className={inputClass} value={category}
                  onChange={(e) => setCategory(e.target.value)}>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </Field>
        <Field label="Version">
          <input className={inputClass} value={version}
                 onChange={(e) => setVersion(e.target.value)} />
        </Field>
        <Field label="Review every (months)"
               hint="12 for the health and safety policy — OHSA s.25(2)(j). Blank if it does not lapse.">
          <input className={inputClass} inputMode="numeric" value={reviewMonths}
                 onChange={(e) => setReviewMonths(e.target.value.replace(/[^0-9]/g, ''))} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Summary">
            <textarea className={`${inputClass} min-h-[70px] py-2`} value={summary}
                      onChange={(e) => setSummary(e.target.value)} />
          </Field>
        </div>
      </div>
      {/* ★ Above the names, which is where HSE asked for it. */}
      <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
        <p className="text-xs font-medium text-neutral-700">The document itself</p>
        <p className="mt-0.5 max-w-[68ch] text-xs text-neutral-500">
          Attach it here. It cannot be published without something to read, so
          adding it now saves opening the record again — and nobody can be asked
          to sign a policy that is not there.
        </p>
        <input ref={fileInput} type="file" className="hidden"
               accept="application/pdf,image/png,image/jpeg,image/webp"
               onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <button type="button" onClick={() => fileInput.current?.click()}
                  className="font-semibold text-primary-700 hover:underline">
            {file ? 'Choose a different file' : 'Attach the document'}
          </button>
          {file && <span className="text-neutral-600">{file.name}</span>}
        </div>
      </div>

      <label className="mt-3 flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" checked={requiresAck}
               onChange={(e) => setRequiresAck(e.target.checked)} />
        This has to be read and signed
      </label>

      {requiresAck && (
        <div className="mt-2 rounded-lg border border-neutral-200 p-3">
          <p className="text-xs font-medium text-neutral-700">Who has to sign it</p>
          <p className="mt-0.5 text-xs text-neutral-500">
            {named.length === 0
              ? 'Everybody, unless you name people here.'
              : 'Only the people named here.'}
          </p>
          {named.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {named.map((p) => (
                <li key={p.userId ?? p.name}
                    className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-700">
                  {p.name}
                  <button type="button" aria-label={`Remove ${p.name}`}
                          onClick={() => setNamed(named.filter((x) => x.userId !== p.userId))}
                          className="flex min-h-[24px] min-w-[24px] items-center justify-center text-neutral-400 hover:text-danger-700">
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-2">
            <PersonPicker
              value={adding}
              onChange={(v) => {
                // Only somebody with an account: the document has to reach a
                // list, and a typed-in name reaches nothing.
                if (v.userId && !named.some((p) => p.userId === v.userId)) {
                  setNamed([...named, v])
                  setAdding(EMPTY_PERSON)
                } else {
                  setAdding(v)
                }
              }}
            />
          </div>
        </div>
      )}
      {partial && (
        <p className="mt-2 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-700">
          {partial}
        </p>
      )}
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!title.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
      <p className="mt-2 text-right text-xs text-neutral-500">
        You will attach the file and publish it on the next screen.
      </p>
    </Card>
  )
}
