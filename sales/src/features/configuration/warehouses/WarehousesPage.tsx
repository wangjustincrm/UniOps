import { z } from 'zod'
import { Badge, CrudPage, useToast, type ColumnDef, type FieldSpec } from '@/platform-ui'
import { AuditHistory } from '../AuditHistory'
import {
  WAREHOUSE_ROLES, downloadWarehouses, fetchWarehouseAudit, warehousesClient,
  type WarehouseCreate, type WarehouseRead, type WarehouseUpdate,
} from './warehousesApi'

const opt = (max: number) => z.string().trim().max(max).nullable().optional()
const base = {
  name: z.string().trim().min(1, 'Required').max(255), role: z.enum(WAREHOUSE_ROLES),
  edi_id_qualifier: opt(5), edi_id: opt(40), address1: opt(255), city: opt(100), province: opt(10), zip: opt(20),
  country: opt(5), address_lines: opt(2000), active: z.boolean(),
}
const createSchema = z.object({ code: z.string().trim().min(1, 'Required').max(50), ...base })
const updateSchema = z.object(base)
const fields: FieldSpec[] = [
  { name: 'code', label: 'Code', required: true },
  { name: 'name', label: 'Name (as on the 943)', required: true },
  { name: 'role', label: 'Role', type: 'select', required: true, options: WAREHOUSE_ROLES.map((r) => ({ value: r, label: r })) },
  { name: 'edi_id_qualifier', label: 'EDI id qualifier', hint: '94 in the warehouse files' },
  { name: 'edi_id', label: 'EDI id', hint: 'e.g. CRM, CLN02' },
  { name: 'address1', label: 'Address' },
  { name: 'city', label: 'City' },
  { name: 'province', label: 'Province' },
  { name: 'zip', label: 'Postal code' },
  { name: 'country', label: 'Country', hint: 'Leave empty to send none (as for Coleraine)' },
  { name: 'address_lines', label: 'Other address lines', type: 'textarea' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]
const columns: ColumnDef<WarehouseRead, unknown>[] = [
  { accessorKey: 'code', header: 'Code' },
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'role', header: 'Role' },
  { accessorKey: 'edi_id', header: 'EDI id' },
  { accessorKey: 'city', header: 'City' },
  { accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge> },
]
const empty = { code: '', name: '', role: 'WAREHOUSE', edi_id_qualifier: '94', edi_id: '', address1: '', city: '', province: '',
  zip: '', country: '', address_lines: '', active: true }

/** Configuration → Warehouses (FR-OB-27): the 943's ship-from and ship-to,
 *  no longer written into the code. */
export default function WarehousesPage() {
  const toast = useToast()
  return (
    <CrudPage<WarehouseRead, WarehouseCreate, WarehouseUpdate>
      title="Warehouses" singular="warehouse"
      description="Where transfers ship from and to; the 943 takes its names and addresses from here."
      client={warehousesClient} columns={columns}
      writePermission="sales.config" createSchema={createSchema} updateSchema={updateSchema} fields={fields}
      immutableOnEdit={['code']} emptyValues={empty}
      toFormValues={(r) => ({ ...r })}
      onExport={(path) => { downloadWarehouses(path).catch((e: unknown) => toast({ variant: 'error', title: 'Export failed', description: e instanceof Error ? e.message : String(e) })) }}
      csvImport
      errorMessages={{ 'warehouse.code_taken': 'A warehouse with this code already exists.' }}
      renderDetail={(r) => <AuditHistory resource="warehouses" id={r.id} version={r.version} load={fetchWarehouseAudit} />}
    />
  )
}
