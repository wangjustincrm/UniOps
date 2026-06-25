import { useSearchParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

export default function PaCreatePage() {
  const [params] = useSearchParams()
  const poId     = params.get('po_id')
  const poNumber = params.get('po_number')
  const source   = params.get('source')

  return (
    <div className="flex flex-col gap-6">
      <div>
        <a href="/pa" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700">
          <ArrowLeft className="h-4 w-4" />
          Back to PA List
        </a>
      </div>

      <div>
        <h1 className="text-2xl font-bold text-neutral-900">
          {poId ? 'PO-Linked Payment Application' : 'New Payment Application'}
        </h1>
        {poId && source === 'epms' && (
          <p className="mt-1 text-sm text-primary-600">
            Creating payment for <span className="font-medium">{poNumber ?? poId}</span> from EPMS
          </p>
        )}
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-6">
        <p className="text-sm text-neutral-500">
          PA create form — coming in OA Sprint 1 backend integration.
        </p>
        {poId && (
          <div className="mt-4 rounded-lg bg-neutral-50 p-4 text-sm">
            <p className="font-medium text-neutral-700">Pre-filled from EPMS PO</p>
            <p className="mt-1 text-neutral-500">PO ID: {poId}</p>
            {poNumber && <p className="text-neutral-500">PO Number: {poNumber}</p>}
          </div>
        )}
      </div>
    </div>
  )
}
