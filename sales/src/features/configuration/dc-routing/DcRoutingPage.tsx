import { z } from 'zod'
import { Badge, CrudPage, useToast, type ColumnDef, type FieldSpec } from '@/platform-ui'
import { AuditHistory } from '../AuditHistory'
import { partnerOptions } from '../lookups'
import {
  dcRoutingClient, downloadDcRouting, fetchDcRoutingAudit,
  type DcRoutingCreate, type DcRoutingRead, type DcRoutingUpdate,
} from './dcRoutingApi'

const required = z.string().trim().min(1, 'Required').max(50)
const base = { dc_number: required, notes: z.string().trim().nullable().optional(), active: z.boolean() }
const createSchema = z.object({ partner_code: required, ship_to_gln: required, ...base })
const updateSchema = z.object(base)
const fields: FieldSpec[] = [
  { name: 'partner_code', label: 'Partner', required: true, type: 'reference', loadOptions: partnerOptions },
  { name: 'ship_to_gln', label: 'Ship-to GLN', required: true },
  { name: 'dc_number', label: 'DC number', required: true },
  { name: 'notes', label: 'Notes', type: 'textarea' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]
const columns: ColumnDef<DcRoutingRead, unknown>[] = [
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'ship_to_gln', header: 'Ship-to GLN' },
  { accessorKey: 'dc_number', header: 'DC' },
  { accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge> },
]

/** Configuration → DC routing (FR-OB-03): a ship-to GLN to its DC, per partner. */
export default function DcRoutingPage() {
  const toast = useToast()
  return (
    <CrudPage<DcRoutingRead, DcRoutingCreate, DcRoutingUpdate>
      title="DC routing" singular="DC route"
      description="For partners that require it (Walmart), an order whose ship-to has no route goes to review."
      client={dcRoutingClient} columns={columns} sortable={['partner_code', 'ship_to_gln', 'dc_number', 'active']}
      writePermission="sales.config" createSchema={createSchema} updateSchema={updateSchema} fields={fields}
      immutableOnEdit={['partner_code', 'ship_to_gln']}
      emptyValues={{ partner_code: '', ship_to_gln: '', dc_number: '', notes: '', active: true }}
      toFormValues={(r) => ({ partner_code: r.partner_code, ship_to_gln: r.ship_to_gln, dc_number: r.dc_number, notes: r.notes, active: r.active })}
      onExport={(path) => { downloadDcRouting(path).catch((e: unknown) => toast({ variant: 'error', title: 'Export failed', description: e instanceof Error ? e.message : String(e) })) }}
      csvImport
      errorMessages={{ 'dc_routing.taken': 'This partner already routes this ship-to GLN.' }}
      renderDetail={(r) => <AuditHistory resource="dc-routing" id={r.id} version={r.version} load={fetchDcRoutingAudit} />}
    />
  )
}
