/**
 * Photographs on a hazard, a JSA or a permit.
 *
 * ★ Round 12, HAZ-03 and JSA-06: "allow pictures on all forms" — this round
 * hazards, JSAs and permits, one table on the server for the three. Who may
 * see and add them follows the record (api/v1/record_attachments.py).
 *
 * Two halves: `RecordPhotos` shows what is on file and adds more to a record
 * that exists; `uploadPhotos` sends the photographs a create form held with
 * PhotoCapture once the record has an id.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, FileText, X } from 'lucide-react'
import { api, apiBlob, ApiError } from '@/lib/api'
import { compressImage, type CompressedImage } from '@/lib/imageCompress'
import { formatDate } from '@/lib/formatDate'
import { LoadFailedNote, SectionTitle } from '@/components/ui'

export type PhotoOwner = 'hazard' | 'jsa' | 'permit'

interface Attachment {
  id: string; filename: string; content_type: string; file_size: number
  uploaded_by_name: string | null; created_at: string
}

const base = (type: PhotoOwner, id: string) => `/api/v1/records/${type}/${id}/attachments`

/** Send what a create form held, once the record exists. Marks each one sent,
 *  so pressing Save again after a failure does not send it twice. */
export async function uploadPhotos(type: PhotoOwner, id: string, photos: CompressedImage[]) {
  for (const photo of photos) {
    if (photo.uploaded) continue
    const blob = await (await fetch(photo.dataUrl)).blob()
    const body = new FormData()
    body.append('file', blob, photo.name)
    await api.upload(base(type, id), body)
    photo.uploaded = true
  }
}

function Thumb({ type, ownerId, att, onRemove, removing }: {
  type: PhotoOwner; ownerId: string; att: Attachment
  onRemove?: () => void; removing: boolean
}) {
  const isImage = att.content_type.startsWith('image/') && att.content_type !== 'image/heic'
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let gone = false
    let objectUrl: string | null = null
    apiBlob(`${base(type, ownerId)}/${att.id}/download`)
      .then((blob) => {
        if (gone) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => setUrl(null))
    return () => {
      gone = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [type, ownerId, att.id])
  return (
    <figure className="relative overflow-hidden rounded-lg border border-neutral-200 bg-white">
      {url ? (
        <a href={url} target="_blank" rel="noreferrer">
          {isImage
            ? <img src={url} alt={att.filename} className="h-28 w-full object-cover hover:opacity-90" />
            : <span className="flex h-28 items-center justify-center bg-neutral-50 text-neutral-400">
                <FileText className="h-7 w-7" aria-hidden />
              </span>}
        </a>
      ) : (
        <span className="flex h-28 items-center justify-center bg-neutral-50 text-neutral-400">
          <FileText className="h-7 w-7" aria-hidden />
        </span>
      )}
      <figcaption className="truncate px-2 py-1.5 text-[11px] text-neutral-500">
        {att.uploaded_by_name ?? '—'}, {formatDate(att.created_at)}
      </figcaption>
      {onRemove && (
        <button type="button" aria-label={`Remove ${att.filename}`} disabled={removing}
                onClick={() => { if (!removing) onRemove() }}
                className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-white/90 text-neutral-600 shadow disabled:opacity-40">
          <X className="h-4 w-4" />
        </button>
      )}
    </figure>
  )
}

export function RecordPhotos({ type, ownerId, canAdd, title = 'Photographs' }: {
  type: PhotoOwner
  ownerId: string
  /** Whether this person may add (and remove) photographs here. */
  canAdd: boolean
  title?: string
}) {
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)
  const key = ['record-photos', type, ownerId]
  const photos = useQuery({ queryKey: key, queryFn: () => api.get<Attachment[]>(base(type, ownerId)) })
  const done = () => { setError(null); queryClient.invalidateQueries({ queryKey: key }) }
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : String(e))
  const add = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) {
        const body = new FormData()
        if (file.type.startsWith('image/')) {
          const small = await compressImage(file)
          body.append('file', await (await fetch(small.dataUrl)).blob(), small.name)
        } else {
          body.append('file', file)
        }
        await api.upload(base(type, ownerId), body)
      }
    },
    onSuccess: done, onError: fail,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`${base(type, ownerId)}/${id}`),
    onSuccess: done, onError: fail,
  })
  const rows = photos.data ?? []
  return (
    <section className="mt-5">
      <SectionTitle className="mb-2">{title}</SectionTitle>
      {photos.isError ? <LoadFailedNote what="the photographs" /> : (
        <>
          {rows.length > 0 ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {rows.map((a) => (
                <Thumb key={a.id} type={type} ownerId={ownerId} att={a}
                       removing={remove.isPending}
                       onRemove={canAdd ? () => remove.mutate(a.id) : undefined} />
              ))}
            </div>
          ) : !photos.isPending && <p className="text-sm text-neutral-500">None attached.</p>}
        </>
      )}
      {error && <p className="mt-2 text-xs text-danger-700">{error}</p>}
      {canAdd && (
        <>
          <input ref={input} type="file" accept="image/*,application/pdf" capture="environment"
                 multiple className="sr-only" aria-label="Add photographs"
                 onChange={(e) => {
                   const files = Array.from(e.target.files ?? [])
                   if (files.length && !add.isPending) add.mutate(files)
                   e.target.value = ''
                 }} />
          <button type="button" disabled={add.isPending} onClick={() => input.current?.click()}
                  className="mt-2 inline-flex min-h-[40px] items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-medium text-neutral-700 disabled:opacity-50">
            <Camera className="h-4 w-4" aria-hidden />
            {add.isPending ? 'Uploading…' : 'Add a photograph'}
          </button>
        </>
      )}
    </section>
  )
}
