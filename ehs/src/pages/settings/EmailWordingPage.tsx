/**
 * ★ UAT 13: "Create a way to change the default email templates that are sent
 * to workers to ensure proper grammar and sentence structure."
 *
 * One card per email: who gets it and when, the subject and the message with
 * the {placeholders} it can use, a preview with example values, and a way back
 * to the default. The server refuses a placeholder the email does not have, so
 * a typo never reaches anybody's inbox as "{ownr_name}".
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { Card, ErrorNote, PageHeader, primaryButton, secondaryButton } from '@/components/ui'

interface Template {
  key: string; name: string; group: string; audience: string; when: string
  placeholders: Record<string, string>
  default_subject: string; default_body: string
  subject: string; body: string; customised: boolean
}
interface Preview { subject: string; html: string; problems: string[] }

const input = 'w-full rounded-lg border border-neutral-300 px-3 text-sm focus:border-primary-400 focus:outline-none'

export default function EmailWordingPage() {
  const list = useQuery({
    queryKey: ['settings', 'email-templates'],
    queryFn: () => api.get<Template[]>('/api/v1/settings/email-templates'),
  })
  return (
    <div className="p-4">
      <PageHeader title="Email wording"
                  subtitle="What every email Safety sends says. Words in {braces} are filled in for each email; the record numbers become links. A line whose {braces} are all empty is left out." />
      {list.isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      <ErrorNote error={list.error} />
      {/* ★ UAT 14: "include all email templates. new incident is missing."
          Every email Safety sends is here, so they are listed by what they are
          about, in the order the server gives. */}
      {groups(list.data ?? []).map(([group, items]) => (
        <section key={group} className="mb-5" aria-label={group}>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">{group}</h2>
          <div className="flex flex-col gap-3">
            {items.map((t) => <TemplateCard key={t.key} t={t} />)}
          </div>
        </section>
      ))}
    </div>
  )
}

// The order HSE meets them in: an incident, what is done about it, and the rest.
const ORDER = ['Incidents', 'Corrective actions', 'JHSC', 'Toolbox talks and inspections',
  'Training', 'Documents', 'Feedback', 'Handing over work']

function groups(all: Template[]): [string, Template[]][] {
  const out = new Map<string, Template[]>()
  for (const t of all) out.set(t.group, [...(out.get(t.group) ?? []), t])
  const rank = (g: string) => (ORDER.indexOf(g) + 1) || ORDER.length + 1
  return [...out.entries()].sort((a, b) => rank(a[0]) - rank(b[0]))
}

function TemplateCard({ t }: { t: Template }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [subject, setSubject] = useState(t.subject)
  const [body, setBody] = useState(t.body)
  const [preview, setPreview] = useState<Preview | null>(null)
  const changed = subject !== t.subject || body !== t.body
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['settings', 'email-templates'] })

  const save = useMutation({
    mutationFn: () => api.put<Template>(`/api/v1/settings/email-templates/${t.key}`, { subject, body }),
    onSuccess: () => { setPreview(null); void refresh() },
  })
  const reset = useMutation({
    mutationFn: () => api.del<Template>(`/api/v1/settings/email-templates/${t.key}`),
    onSuccess: (r) => { setSubject(r.subject); setBody(r.body); setPreview(null); void refresh() },
  })
  const show = useMutation({
    mutationFn: () => api.post<Preview>(`/api/v1/settings/email-templates/${t.key}/preview`, { subject, body }),
    onSuccess: setPreview,
  })
  const busy = save.isPending || reset.isPending

  return (
    <Card>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 text-left">
        <span className="text-sm font-semibold text-neutral-900">{t.name}</span>
        {t.customised && (
          <span className="rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">Your wording</span>
        )}
        <span className="min-w-0 flex-1 text-xs text-neutral-500">{t.when} · to {t.audience.toLowerCase()}</span>
      </button>
      {open && (
        <div className="mt-3">
          <label className="block text-xs font-medium text-neutral-700">
            Subject
            <input className={`${input} mt-1 min-h-[40px]`} value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="mt-2 block text-xs font-medium text-neutral-700">
            Message
            <textarea rows={12} className={`${input} mt-1 py-2 font-mono text-[13px]`} value={body}
                      onChange={(e) => setBody(e.target.value)} />
          </label>
          <div className="mt-2">
            <p className="text-xs font-medium text-neutral-700">It can use</p>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {Object.entries(t.placeholders).map(([k, why]) => (
                <li key={k}>
                  <button type="button" title={`${why} — click to add it to the message`}
                          onClick={() => setBody((b) => `${b}{${k}}`)}
                          className="rounded-md border border-neutral-200 bg-neutral-50 px-2 py-0.5 font-mono text-xs text-neutral-700 hover:border-primary-300">
                    {`{${k}}`}
                  </button>
                  <span className="sr-only">{why}</span>
                </li>
              ))}
            </ul>
          </div>
          <ErrorNote error={save.error || reset.error || show.error} />
          {preview && preview.problems.length > 0 && (
            <ul className="mt-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {preview.problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          {preview && preview.problems.length === 0 && (
            <div className="mt-3 rounded-lg border border-neutral-200">
              <p className="border-b border-neutral-200 bg-neutral-50 px-3 py-1.5 text-xs text-neutral-600">
                Subject: <b className="text-neutral-900">{preview.subject}</b> · with example values
              </p>
              {/* The server's own rendering; sandboxed, and nothing in it runs. */}
              <iframe title={`${t.name} preview`} sandbox="" srcDoc={preview.html}
                      className="h-80 w-full rounded-b-lg bg-white" />
            </div>
          )}
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            {t.customised && (
              <button type="button" className={secondaryButton} disabled={busy}
                      onClick={() => { if (!busy && window.confirm('Put this email back to the default wording?')) reset.mutate() }}>
                Back to the default
              </button>
            )}
            <button type="button" className={secondaryButton} disabled={show.isPending}
                    onClick={() => { if (!show.isPending) show.mutate() }}>
              {show.isPending ? 'Preparing…' : 'Preview'}
            </button>
            <button type="button" className={primaryButton} disabled={!changed || busy}
                    onClick={() => { if (changed && !busy) save.mutate() }}>
              {save.isPending ? 'Saving…' : 'Save the wording'}
            </button>
          </div>
          {save.isError && save.error instanceof ApiError && (
            <p className="mt-1 text-right text-xs text-neutral-500">Nothing was saved.</p>
          )}
        </div>
      )}
    </Card>
  )
}
