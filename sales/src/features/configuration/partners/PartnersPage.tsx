import { Badge, CrudPage, type ColumnDef , columnsOf } from '@/platform-ui'
import { historyTab } from '../historyTab'
import { downloadPartners, fetchPartnerAudit, partnersClient, type PartnerCreate, type PartnerRead, type PartnerUpdate } from './partnersApi'
import {
  partnerCreateSchema, partnerEmptyValues, partnerFields, partnerToFormValues, partnerUpdateSchema,
} from './partnerSchema'

const colPartnerRead = columnsOf<PartnerRead>()

const columns: ColumnDef<PartnerRead, unknown>[] = [
  colPartnerRead.text('code', 'Code'),
  colPartnerRead.text('name', 'Name'),
  colPartnerRead.text('channel', 'Channel'),
  colPartnerRead.text('settlement_mode', 'Settlement'),
  colPartnerRead.text('invoice_method', 'Invoice'),
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
  return (
    <CrudPage<PartnerRead, PartnerCreate, PartnerUpdate>
      title="Partners"
      singular="partner"
      description="Trading partners: channel, settlement, sending and invoicing rules."
      client={partnersClient}
      columns={columns}
      writePermission="sales.config"
      createSchema={partnerCreateSchema}
      updateSchema={partnerUpdateSchema}
      fields={partnerFields}
      immutableOnEdit={['code']}
      emptyValues={partnerEmptyValues}
      toFormValues={partnerToFormValues}
      onExport={(path) => downloadPartners(path)}
      csvImport
      errorMessages={errorMessages}
      recordTitle={(p) => p.name}
      recordSubtitle={(p) => `${p.code} · ${p.channel}`}
      detailTabs={(p, details) => [details, historyTab('partners', p.id, p.version, fetchPartnerAudit)]}
    />
  )
}
