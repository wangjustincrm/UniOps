import { Badge, CrudPage, useToast, type ColumnDef } from '@/platform-ui'
import { AuditHistory } from './AuditHistory'
import { downloadPartners, partnersClient, type PartnerCreate, type PartnerRead, type PartnerUpdate } from './partnersApi'
import {
  partnerCreateSchema, partnerEmptyValues, partnerFields, partnerToFormValues, partnerUpdateSchema,
} from './partnerSchema'

const columns: ColumnDef<PartnerRead, unknown>[] = [
  { accessorKey: 'code', header: 'Code' },
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'channel', header: 'Channel' },
  { accessorKey: 'settlement_mode', header: 'Settlement' },
  { accessorKey: 'invoice_method', header: 'Invoice' },
  {
    accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge>,
  },
]

const errorMessages: Record<string, string> = {
  'partner.code_taken': 'A partner with this code already exists.',
  'partner.invoice_email_required': 'Required when the invoice method is EMAIL.',
  'entity.stale': 'Someone else changed this partner. Close the form and open it again.',
  'auth.forbidden': 'You do not have permission to change partners.',
}

/** Configuration → Partners (FR-OB-03). Everyone with sales.read sees the
 *  list and each partner read-only; sales.config edits, deactivates and imports. */
export default function PartnersPage() {
  const toast = useToast()
  return (
    <CrudPage<PartnerRead, PartnerCreate, PartnerUpdate>
      title="Partners"
      singular="partner"
      description="Trading partners: channel, settlement, sending and invoicing rules."
      client={partnersClient}
      columns={columns}
      sortable={['code', 'name', 'channel', 'settlement_mode', 'active']}
      writePermission="sales.config"
      createSchema={partnerCreateSchema}
      updateSchema={partnerUpdateSchema}
      fields={partnerFields}
      immutableOnEdit={['code']}
      emptyValues={partnerEmptyValues}
      toFormValues={partnerToFormValues}
      onExport={(path) => {
        downloadPartners(path).catch((e: unknown) =>
          toast({ variant: 'error', title: 'Export failed', description: e instanceof Error ? e.message : String(e) }))
      }}
      csvImport
      errorMessages={errorMessages}
      renderDetail={(p) => <AuditHistory id={p.id} version={p.version} />}
    />
  )
}
