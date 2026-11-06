import { z } from 'zod'
import { Badge, CrudPage, type ColumnDef, type FieldSpec , columnsOf } from '@/platform-ui'
import { historyTab } from '../historyTab'
import {
  carriersClient, downloadCarriers, fetchCarrierAudit,
  type CarrierCreate, type CarrierRead, type CarrierUpdate,
} from './carriersApi'

const colCarrierRead = columnsOf<CarrierRead>()

const template = z.string().trim().max(500)
  .refine((v) => v === '' || v.startsWith('https://'), 'An https:// address')
  .refine((v) => v === '' || v.includes('{tracking}'), 'Put {tracking} where the tracking number goes')
  .nullable().optional()
const base = { name: z.string().trim().min(1, 'Required').max(255), tracking_url: template, active: z.boolean() }
const createSchema = z.object({ scac: z.string().trim().min(2, 'Required').max(10), ...base })
const updateSchema = z.object(base)
const fields: FieldSpec[] = [
  { name: 'scac', label: 'SCAC', required: true, hint: "The 945's W27 carrier code, e.g. PRLA" },
  { name: 'name', label: 'Name', required: true },
  { name: 'tracking_url', label: 'Tracking link', hint: 'https://… with {tracking} where the number goes; empty = the number is shown without a link' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]
const columns: ColumnDef<CarrierRead, unknown>[] = [
  colCarrierRead.text('scac', 'SCAC'),
  colCarrierRead.text('name', 'Name'),
  { accessorKey: 'tracking_url', header: 'Tracking link', cell: ({ row }) => row.original.tracking_url ?? '—' },
  { accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge> },
]
const empty = { scac: '', name: '', tracking_url: '', active: true }

/** Configuration → Carriers: the 945's SCAC in words and the link a tracking
 *  number opens (on the shipment, its rows, Lots and the packing list). */
export default function CarriersPage() {
  return (
    <CrudPage<CarrierRead, CarrierCreate, CarrierUpdate>
      title="Carriers" singular="carrier"
      description="The carriers on the 945s: their names, and where a tracking number is looked up."
      client={carriersClient} columns={columns}
      writePermission="sales.config" createSchema={createSchema} updateSchema={updateSchema} fields={fields}
      immutableOnEdit={['scac']} emptyValues={empty}
      toFormValues={(r) => ({ ...r, tracking_url: r.tracking_url ?? '' })}
      onExport={(path) => downloadCarriers(path)}
      csvImport
      errorMessages={{ 'carrier.scac_taken': 'A carrier with this SCAC already exists.' }}
      recordTitle={(r) => r.name}
      recordSubtitle={(r) => r.scac}
      detailTabs={(r, details) => [details, historyTab('carriers', r.id, r.version, fetchCarrierAudit)]}
    />
  )
}
