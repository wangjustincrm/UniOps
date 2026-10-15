/**
 * Training material — files and links — on a course or on one session.
 *
 * Round 6, TC-06: "transfer a copy over to the session. add the ability to add
 * a url and a video, all file types would be good." The course and the session
 * hold the same kind of item at the same kind of address (`base`), so one list
 * serves both, rather than two that drift.
 *
 * A link opens in a new tab and nothing about it is fetched here. A file is
 * fetched with the session's token and opened through `openFile`, which shows
 * only what cannot carry script and downloads the rest.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Link2, Paperclip, X } from 'lucide-react'
import { api, useFileOpener } from '@/lib/api'
import { ErrorNote, inputClass, secondaryButton } from '@/components/ui'

export interface Material {
  id: string; filename: string; content_type: string; file_size: number
  url: string | null; uploaded_by_name: string | null; created_at: string
}

export default function MaterialsList({ base, mayWrite, what = 'this course' }: {
  /** e.g. /api/v1/training/courses/{id}/materials */
  base: string
  mayWrite: boolean
  what?: string
}) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [linking, setLinking] = useState(false)
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const opener = useFileOpener()

  const materials = useQuery({
    queryKey: ['materials', base],
    queryFn: () => api.get<Material[]>(base),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['materials', base] })

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(base, body)
    },
    onSuccess: refresh,
  })
  const addLink = useMutation({
    mutationFn: () => api.post(`${base}/link`, { url: url.trim(), title: title.trim() || null }),
    onSuccess: () => { setUrl(''); setTitle(''); setLinking(false); refresh() },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`${base}/${id}`),
    onSuccess: refresh,
  })

  // ★ A failure to load must not look the same as there being none — the
  // third round reported exactly that as "the instructor cannot open the files".
  const items = materials.data ?? []
  if (!mayWrite && items.length === 0 && !materials.isError && !materials.isPending) return null

  return (
    <div className="flex flex-col gap-1 text-xs text-neutral-600">
      {materials.isPending ? (
        <span className="text-neutral-500">Loading the material…</span>
      ) : materials.isError ? (
        <span className="text-warning-700">Could not load the material for {what}</span>
      ) : items.length === 0 ? (
        <span className="text-neutral-500">No material</span>
      ) : null}

      {items.map((m) => (
        <span key={m.id} className="flex items-center gap-1.5">
          {m.url ? (
            <>
              <Link2 className="h-3.5 w-3.5 shrink-0 text-neutral-400" aria-hidden />
              <a href={m.url} target="_blank" rel="noopener noreferrer"
                 className="inline-flex min-w-0 items-center gap-1 truncate text-primary-700 hover:underline">
                {m.filename}
                <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
              </a>
            </>
          ) : (
            <>
              <Paperclip className="h-3.5 w-3.5 shrink-0 text-neutral-400" aria-hidden />
              <button type="button" disabled={opener.busy}
                      onClick={() => opener.open(`${base}/${m.id}`, m.filename)}
                      className="min-w-0 truncate text-left text-primary-700 hover:underline">
                {m.filename}
              </button>
            </>
          )}
          {mayWrite && (
            <button type="button" aria-label={`Remove ${m.filename}`}
                    disabled={remove.isPending}
                    onClick={() => { if (!remove.isPending) remove.mutate(m.id) }}
                    className="shrink-0 rounded p-0.5 text-neutral-400 hover:bg-neutral-50">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </span>
      ))}

      {opener.error && <span className="text-danger-700">{opener.error}</span>}

      {mayWrite && (
        <>
          <input ref={inputRef} type="file" className="hidden"
                 onChange={(e) => {
                   const file = e.target.files?.[0]
                   if (file) upload.mutate(file)
                   e.target.value = ''
                 }} />
          <span className="flex flex-wrap gap-x-3">
            <button type="button" disabled={upload.isPending}
                    onClick={() => inputRef.current?.click()}
                    className="rounded-md font-medium text-primary-700 disabled:opacity-40">
              {upload.isPending ? 'Uploading…' : 'Attach a file'}
            </button>
            <button type="button" onClick={() => setLinking((v) => !v)}
                    className="rounded-md font-medium text-primary-700">
              Add a link
            </button>
          </span>
          <span className="text-[11px] text-neutral-500">
            PDF, Word, Excel, PowerPoint, pictures, text, video or sound (MP4, MOV, WebM, MP3, M4A,
            WAV), up to 50 MB. A long video goes in as a link.
          </span>
          {linking && (
            <span className="mt-1 flex flex-col gap-1.5 sm:flex-row">
              <input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)}
                     placeholder="https://…" aria-label="Link address" />
              <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                     placeholder="What it is (optional)" aria-label="Link title" />
              <button type="button" className={secondaryButton}
                      disabled={!url.trim() || addLink.isPending}
                      onClick={() => { if (!addLink.isPending) addLink.mutate() }}>
                {addLink.isPending ? 'Adding…' : 'Add'}
              </button>
            </span>
          )}
          <ErrorNote error={upload.error || addLink.error || remove.error} />
        </>
      )}
    </div>
  )
}
