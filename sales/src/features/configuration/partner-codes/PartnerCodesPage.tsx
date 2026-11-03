import { z } from 'zod'
import { CrudPage, useToast, type ColumnDef, type FieldSpec } from '@/platform-ui'
import { historyTab } from '../historyTab'
import { itemOptions, partnerOptions } from '../lookups'
import {
  downloadPartnerCodes, fetchPartnerCodeAudit, partnerCodesClient,
  type PartnerCodeCreate, type PartnerCodeRead, type PartnerCodeUpdate,
} from './partnerCodesApi'

const required = z.string().trim().min(1, 'Required').max(100)
const createSchema = z.object({ partner_code: required, buyer_part: required, item_code: required })
const updateSchema = z.object({ item_code: required })
const fields: FieldSpec[] = [
  { name: 'partner_code', label: 'Partner', required: true, type: 'reference', loadOptions: partnerOptions },
  { name: 'buyer_part', label: 'Buyer part', required: true, hint: "This partner's own number for the item; it never matches another partner's lines." },
  { name: 'item_code', label: 'Item', required: true, type: 'reference', loadOptions: itemOptions },
]
const columns: ColumnDef<PartnerCodeRead, unknown>[] = [
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'buyer_part', header: 'Buyer part' },
  { accessorKey: 'item_code', header: 'Item' },
]

/** Configuration → Partner codes (FR-OB-02): buyer part numbers per partner. */
export default function PartnerCodesPage() {
  const toast = useToast()
  return (
    <CrudPage<PartnerCodeRead, PartnerCodeCreate, PartnerCodeUpdate>
      title="Partner codes" singular="partner code"
      description="Each retailer's own part numbers for our items. A key is partner + buyer part."
      client={partnerCodesClient} columns={columns}
      writePermission="sales.config" createSchema={createSchema} updateSchema={updateSchema} fields={fields}
      immutableOnEdit={['partner_code', 'buyer_part']}
      emptyValues={{ partner_code: '', buyer_part: '', item_code: '' }}
      toFormValues={(r) => ({ partner_code: r.partner_code, buyer_part: r.buyer_part, item_code: r.item_code })}
      onExport={(path) => { downloadPartnerCodes(path).catch((e: unknown) => toast({ variant: 'error', title: 'Export failed', description: e instanceof Error ? e.message : String(e) })) }}
      csvImport
      errorMessages={{ 'partner_code.taken': 'This partner already uses this buyer part.' }}
      recordTitle={(r) => `${r.partner_code} ${r.buyer_part}`}
      recordSubtitle={(r) => `→ ${r.item_code}`}
      detailTabs={(r, details) => [details, historyTab('partner-codes', r.id, r.version, fetchPartnerCodeAudit)]}
    />
  )
}
