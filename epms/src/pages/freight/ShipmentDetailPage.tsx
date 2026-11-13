import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Loader2, Pencil } from 'lucide-react'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { NotFoundState } from '@/components/shared/NotFoundState'
import { Button } from '@/components/ui/button'
import { formatDate } from '@/lib/utils'
import { useFreightShipment } from '@/hooks/useFreight'
import { ShipmentStatusBadge } from './freightUi'
import { ShipmentAttachmentsPanel } from './ShipmentAttachments'
import { ShipmentDialog } from './ShipmentsTab'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="text-sm text-neutral-800">{children}</dd>
    </div>
  )
}

/** Freight → Shipments → View. Who may open it is who may see the shipment in
 *  the list (own shipments for a registrar, all for a freight manager). */
export default function ShipmentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: s, isPending, isError } = useFreightShipment(id)
  const [editing, setEditing] = useState(false)
  useDocTabTitle(s ? `Shipment ${s.tracking_no}` : undefined)

  if (isPending) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-300" />
      </div>
    )
  }
  if (isError || !s) {
    return (
      <NotFoundState
        title="Shipment Not Found"
        message="This shipment does not exist, or it is not one you can see."
        backTo="/freight" backLabel="Back to Freight"
      />
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <BackLink to="/freight" className="mb-2 inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-700">
            <ArrowLeft className="h-4 w-4" /> Back to Freight
          </BackLink>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-2xl font-bold text-neutral-900">{s.tracking_no}</h1>
            <ShipmentStatusBadge status={s.status} />
          </div>
          <p className="mt-0.5 text-sm text-neutral-500">
            {s.carrier_code ?? '—'} shipment · registered by {s.created_by_name ?? '—'} on {formatDate(s.created_at)}
          </p>
        </div>
        {s.status === 'pending' && (
          <Button variant="secondary" className="gap-2" onClick={() => setEditing(true)}>
            <Pencil className="h-4 w-4" /> Edit
          </Button>
        )}
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold text-neutral-700">Shipment</h2>
        <dl className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Carrier">{s.carrier_code ?? '—'}</Field>
          <Field label="Tracking No"><span className="font-mono">{s.tracking_no}</span></Field>
          <Field label="Direction">
            {s.direction === 'outbound' ? 'Outbound (we send)' : 'Inbound (sent to us, we pay)'}
          </Field>
          <Field label="Department">{s.department_name ?? '—'}</Field>
          <Field label="Cost Center">
            {s.cost_center_code ? `${s.cost_center_code} — ${s.cost_center_name ?? ''}` : '—'}
          </Field>
          <Field label="Recorded">
            {s.source === 'from_invoice' ? 'Added while matching an invoice' : 'Registered at ship time'}
          </Field>
          <Field label="Ship Date">{s.ship_date ? formatDate(s.ship_date) : <span className="text-neutral-400">Filled from the invoice once billed</span>}</Field>
          <Field label="Invoice">
            {s.invoice_id ? (
              <Link to={`/invoices/${s.invoice_id}`} className="font-mono text-primary-700 hover:underline">
                {s.invoice_number ?? 'Invoice'}
              </Link>
            ) : <span className="text-neutral-400">Not billed yet</span>}
          </Field>
          <Field label="Billed (before tax)">
            {s.billed_amount != null
              ? Number(s.billed_amount).toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
              : '—'}
          </Field>
          <div className="sm:col-span-2 lg:col-span-3">
            <Field label="Note">
              {s.note ? <span className="whitespace-pre-wrap">{s.note}</span> : <span className="text-neutral-400">—</span>}
            </Field>
          </div>
        </dl>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold text-neutral-700">Attachments</h2>
        <p className="mb-4 text-xs text-neutral-400">
          {s.status === 'void'
            ? 'This shipment is void — its files can be viewed but not changed.'
            : 'The scanned shipping label and any supporting documents.'}
        </p>
        <ShipmentAttachmentsPanel shipmentId={s.id} editable={s.status !== 'void'} />
      </div>

      {editing && <ShipmentDialog shipment={s} onClose={() => setEditing(false)} />}
    </div>
  )
}
