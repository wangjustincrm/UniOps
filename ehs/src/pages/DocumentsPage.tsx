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
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { FileText, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, Empty, ErrorNote, Field, PageHeader, Tabs, inputClass, primaryButton, secondaryButton,
} from '@/components/ui'

export interface SafetyDoc {
  id: string; doc_no: string; title: string; category: string; summary: string | null
  version: string; file_id: string | null; filename: string | null
  effective_on: string | null; review_due_on: string | null; review_months: number | null
  status: string; published_at: string | null; published_by_name: string | null
  supersedes_id: string | null; requires_ack: boolean; ack_roles: string[]
  qr_token: string | null; owner_name: string | null
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
      {mayManage && statuses.includes('published') && (
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
            <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
              {CATEGORY_LABELS[category] ?? category}
            </h2>
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
                      <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-500">
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

  const create = useMutation({
    mutationFn: () => api.post<SafetyDoc>('/api/v1/documents', {
      title, category, summary: summary || null, version,
      review_months: reviewMonths ? Number(reviewMonths) : null,
      requires_ack: requiresAck,
    }),
    onSuccess: (doc) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      onClose()
      navigate(`/documents/${doc.id}`)
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
      <label className="mt-3 flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" checked={requiresAck}
               onChange={(e) => setRequiresAck(e.target.checked)} />
        Everyone must read and sign this
      </label>
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
