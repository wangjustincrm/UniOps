/**
 * What the code on a chemical label opens: the data sheet in force today.
 *
 * ★ Round 7, TC-07: "scanning the qr code on mobile device opens the chemical
 * register and does not open the SDS sheet and it should." A label stays on a
 * bottle for years and the sheet is revised under it, so the code names the
 * product and the sheet is looked up when it is scanned — never the revision
 * that was current on the day the label was printed.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { api, apiBlob, ApiError } from '@/lib/api'
import { primaryButton } from '@/components/ui'

interface CurrentSheet {
  chemical_name: string; sds_id: string | null; filename: string | null; has_file: boolean
}

export default function CurrentSdsPage() {
  const { id = '' } = useParams()
  const current = useQuery({
    queryKey: ['current-sds', id],
    queryFn: () => api.get<CurrentSheet>(`/api/v1/chemicals/${encodeURIComponent(id)}/sds/current`),
    enabled: Boolean(id),
    retry: false,
  })
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  // Fetched with the session's token and shown in this tab: on a phone a new
  // window is usually blocked, and the person scanning wants the sheet, not a
  // second step.
  useEffect(() => {
    const sheet = current.data
    if (!sheet?.has_file || !sheet.sds_id) return
    let made: string | null = null
    apiBlob(`/api/v1/sds/${sheet.sds_id}/file`)
      .then((blob) => { made = URL.createObjectURL(blob); setUrl(made); window.location.assign(made) })
      .catch((e) => setFailed(e instanceof ApiError && e.status === 403
        ? 'Your account cannot open safety data sheets. Ask HSE or your supervisor for the sheet.'
        : e instanceof ApiError ? e.message : 'The data sheet could not be opened. Scan again.'))
    return () => { if (made) setTimeout(() => URL.revokeObjectURL(made as string), 60_000) }
  }, [current.data])

  const sheet = current.data
  return (
    <div className="mx-auto max-w-md p-5 text-center">
      <FileText className="mx-auto h-8 w-8 text-primary-600" aria-hidden />
      <h1 className="mt-2 text-lg font-semibold text-neutral-900">
        {sheet ? `Safety data sheet — ${sheet.chemical_name}` : 'Safety data sheet'}
      </h1>
      {current.isLoading && <p className="mt-2 text-base text-neutral-600">Finding the current sheet…</p>}
      {current.error && (
        <p className="mt-2 text-base text-danger-700">
          {current.error instanceof ApiError && current.error.status === 404
            ? 'This code does not match a product in the register.'
            : current.error instanceof ApiError && current.error.status === 403
              ? 'Your account cannot open safety data sheets. Ask HSE or your supervisor for the sheet.'
              : 'The data sheet could not be looked up. Check the connection and scan again.'}
        </p>
      )}
      {sheet && !sheet.has_file && (
        <p className="mt-2 text-base text-warning-800">
          There is no current data sheet on file for this product. Tell HSE — the product
          should not be used without one.
        </p>
      )}
      {sheet?.has_file && !failed && (
        <p className="mt-2 text-base text-neutral-600">Opening the current sheet…</p>
      )}
      {failed && <p className="mt-2 text-base text-danger-700">{failed}</p>}
      {url && (
        <a href={url} className={`${primaryButton} mt-4 inline-flex`}>Open the data sheet</a>
      )}
      <p className="mt-6 text-sm text-neutral-500">
        Always the sheet in force today, whichever revision the label was printed with.
      </p>
      <Link to={`/chemicals/${id}`} className="mt-2 inline-block text-sm font-semibold text-primary-700 hover:underline">
        The product in the chemical register
      </Link>
    </div>
  )
}
