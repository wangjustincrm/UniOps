import { Plus, Trash2 } from 'lucide-react'
import { formatAmount, formatDate, formatUnitPrice } from '@/lib/utils'
import { lineTotalOf } from '@/lib/importedPoEdit'

import { Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
/** One line of an NC-imported PO as the buyer-detail form sees it.
 *
 *  Two kinds share this table. On an NC-sourced line everything except
 *  supplierItemId and sample is display-only: the ERP owns those values and the
 *  endpoint cannot write them. On a buyer-added line — a one-off charge such as
 *  a mould/tooling quote the supplier wants itemised, which NC has no way to
 *  carry — every field is the buyer's, and the line can be removed again. */
export interface ImportedPoLine {
  id: string
  /** False on a buyer-added line. Decides which cells are inputs. */
  ncSourced: boolean
  description: string
  materialId?: string | null
  qty: number
  unit: string
  unitPrice: number
  lineTotal: number
  supplierItemId: string
  sample: string
  // ERP-synced per-line arrival date. Display-only — there is no field on
  // PoImportedDetailsUpdate to write it back, and an added line has none.
  plannedArrivalDate?: string | null
}

interface ImportedPoLineItemsProps {
  items: ImportedPoLine[]
  onChange: (items: ImportedPoLine[]) => void
  currency: string
  /** Whether rows may be added, edited or removed at all. */
  canAddLines?: boolean
  /** Locked because an invoice already points at this PO. */
  locked?: boolean
}

let addedSeq = 0

/** Line table for the imported-PO buyer-detail form.
 *
 *  Intentionally NOT PrLineItems: that component serves four pages and carries
 *  pickers, reordering and validation shaped around a document UniOps owns
 *  outright. Here most rows belong to the ERP and only some are the buyer's.
 */
export function ImportedPoLineItems({
  items, onChange, currency, canAddLines = false, locked = false,
}: ImportedPoLineItemsProps) {
  const editable = canAddLines && !locked

  const update = (index: number, patch: Partial<ImportedPoLine>) => {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  const addRow = () => {
    // A client-side id so React can key the row and the buyer can edit it
    // before it exists server-side. Stripped from the payload: an entry with no
    // id is what tells the endpoint to create the line.
    addedSeq += 1
    onChange([...items, {
      id: `new-${addedSeq}`, ncSourced: false, description: '',
      materialId: null, qty: 1, unit: 'EA', unitPrice: 0, lineTotal: 0,
      supplierItemId: '', sample: '', plannedArrivalDate: null,
    }])
  }

  const removeRow = (index: number) => {
    onChange(items.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-line">
        <Table density="compact" className="min-w-[980px]">
          <THead>
            <Tr className="bg-surface-subtle text-left text-xs text-fg-muted">
              <Th className="w-10">#</Th>
              <Th>Description</Th>
              <Th className="w-32">Material ID</Th>
              <Th align="right" className="w-24 ">Qty</Th>
              <Th className="w-20">Unit</Th>
              <Th align="right" className="w-28 ">Unit Price</Th>
              <Th align="right" className="w-28 ">Line Total</Th>
              <Th className="w-28">Delivery Date</Th>
              <Th className="w-40">Supplier Item ID</Th>
              <Th className="w-32">Sample (g or ea)</Th>
              <Th className="w-10" />
            </Tr>
          </THead>
          <TBody>
            {items.map((item, i) => (
              <Tr key={item.id}>
                <Td className="text-fg-muted">{i + 1}</Td>
                <Td className="text-fg">
                  {item.ncSourced ? item.description : (
                    <Input
                      type="text"
                      value={item.description}
                      onChange={(e) => update(i, { description: e.target.value })}
                      placeholder="e.g. Mould tooling"
                      aria-label={`Description for added line ${i + 1}`}
                      maxLength={500}
                      disabled={locked}
                      inputSize="sm"
                    />
                  )}
                </Td>
                <Td className="font-mono text-xs text-fg-muted">
                  {item.ncSourced ? (item.materialId || '—') : (
                    // A buyer-added line has no material code by design — that
                    // is what keeps a one-off charge out of MRP's supply.
                    <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-sans text-2xs text-fg-secondary">
                      Added
                    </span>
                  )}
                </Td>
                <Td align="right" className="font-mono text-fg-secondary">
                  {item.ncSourced ? item.qty : (
                    <Input
                      type="number" step="any" min="0"
                      value={item.qty}
                      onChange={(e) => update(i, { qty: Number(e.target.value) })}
                      aria-label={`Quantity for added line ${i + 1}`}
                      disabled={locked}
                      inputSize="sm" className="text-right font-mono"
                    />
                  )}
                </Td>
                <Td className="text-fg-secondary">
                  {item.ncSourced ? item.unit : (
                    <Input
                      type="text"
                      value={item.unit}
                      onChange={(e) => update(i, { unit: e.target.value })}
                      aria-label={`Unit for added line ${i + 1}`}
                      maxLength={30}
                      disabled={locked}
                      inputSize="sm"
                    />
                  )}
                </Td>
                <Td align="right" className="font-mono text-fg-secondary">
                  {item.ncSourced ? formatUnitPrice(item.unitPrice, currency) : (
                    <Input
                      type="number" step="any"
                      value={item.unitPrice}
                      onChange={(e) => update(i, { unitPrice: Number(e.target.value) })}
                      aria-label={`Unit price for added line ${i + 1}`}
                      disabled={locked}
                      inputSize="sm" className="text-right font-mono"
                    />
                  )}
                </Td>
                <Td align="right" className="font-mono text-fg">
                  {formatAmount(
                    item.ncSourced ? item.lineTotal : lineTotalOf(item),
                    currency,
                  )}
                </Td>
                <Td className="text-fg-muted">
                  {item.plannedArrivalDate ? formatDate(item.plannedArrivalDate) : '—'}
                </Td>
                <Td>
                  <Input
                    type="text"
                    value={item.supplierItemId}
                    onChange={(e) => update(i, { supplierItemId: e.target.value })}
                    placeholder="SKU / catalog #"
                    aria-label={`Supplier item ID for line ${i + 1}`}
                    maxLength={100}
                    inputSize="sm" className="font-mono"
                  />
                </Td>
                <Td>
                  <Input
                    type="text"
                    value={item.sample}
                    onChange={(e) => update(i, { sample: e.target.value })}
                    placeholder="e.g. 500 g"
                    aria-label={`Sample requirement for line ${i + 1}`}
                    maxLength={100}
                    inputSize="sm"
                  />
                </Td>
                <Td align="right">
                  {!item.ncSourced && editable && (
                    <Button variant="danger-ghost" size="icon-xs"
                      type="button"
                      onClick={() => removeRow(i)}
                      aria-label={`Remove added line ${i + 1}`}
                     
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>

      {editable && (
        <Button variant="link"
          type="button"
          onClick={addRow}
          className="text-sm"
        >
          <Plus className="h-4 w-4" />
          Add Row
        </Button>
      )}
      {canAddLines && locked && (
        <p className="text-xs text-fg-muted">
          Added lines are locked — an invoice has been raised against this PO.
        </p>
      )}
    </div>
  )
}
