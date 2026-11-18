import { useState } from 'react'
import { AlertTriangle, Check, ChevronRight, ChevronDown, Package, Warehouse, FileText, CreditCard } from 'lucide-react'
import { Link } from 'react-router-dom'
import { formatCAD } from '@/lib/utils'
import { StatusBadge } from '@/components/ui/badge'
import type { DocumentStatus } from '@/types'

interface GrInfo {
  id: string
  number: string
  type: 'physical' | 'service'
  status: DocumentStatus
  collectionPending?: boolean
  acknowledged?: boolean
}

interface InvoiceInfo {
  id: string
  number: string
  status: DocumentStatus
  amount: number
}

interface PaInfo {
  id: string
  number?: string
  status: DocumentStatus
  locked?: boolean
  lockReason?: string
}

interface PoInfo {
  id: string
  number: string
  status: DocumentStatus
  vendor: string
  grs: GrInfo[]
  invoices: InvoiceInfo[]
  pa?: PaInfo
}

export interface PrPipelineItem {
  id: string
  number: string
  title: string
  status: DocumentStatus
  amount: number
  pos: PoInfo[]
}

interface PrRowProps {
  pr: PrPipelineItem
}

function PrRow({ pr }: PrRowProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-xl bg-surface shadow-sm overflow-hidden">
      {/* PR header */}
      <div
        className="flex items-center gap-3 px-4 py-3 hover:bg-surface-subtle cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
        role="button"
        aria-expanded={expanded}
      >
        {/* The whole row is the toggle (role=button above); this is only its chevron. */}
        <span aria-hidden className="text-fg-muted">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Link
              to={`/pr/${pr.id}`}
              className="text-sm font-medium text-fg hover:text-primary-600 truncate"
              onClick={(e) => e.stopPropagation()}
            >
              {pr.number}
            </Link>
            <StatusBadge status={pr.status} />
          </div>
          <p className="text-xs text-fg-muted truncate mt-0.5">{pr.title}</p>
        </div>
        <span className="amount text-sm text-fg-secondary shrink-0">{formatCAD(pr.amount)}</span>
      </div>

      {/* Linked documents */}
      {expanded && (
        <div className="border-t border-line-subtle bg-surface-subtle px-4 py-2">
          {pr.pos.length === 0 && (
            <p className="py-2 text-xs text-fg-muted italic">No PO issued yet</p>
          )}
          {pr.pos.map((po) => (
            <div key={po.id} className="mb-2">
              {/* PO row */}
              <div className="flex items-center gap-2 py-1.5 pl-4 border-l-2 border-primary-300">
                <Package className="h-3.5 w-3.5 text-fg-muted shrink-0" />
                <Link to={`/po/${po.id}`} className="text-xs font-medium text-primary-600 hover:underline">
                  {po.number}
                </Link>
                <StatusBadge status={po.status} />
                <span className="text-xs text-fg-muted ml-1">→ {po.vendor}</span>
              </div>

              {/* GRs */}
              {po.grs.map((gr) => (
                <div key={gr.id} className="flex items-center gap-2 py-1 pl-10 border-l-2 border-primary-200 ml-2">
                  <Warehouse className="h-3 w-3 text-fg-muted shrink-0" />
                  <Link to={`/gr/${gr.id}`} className="text-xs text-fg-secondary hover:text-accent-fg">
                    {gr.number}
                  </Link>
                  <StatusBadge status={gr.status} />
                  {gr.acknowledged && <span className="inline-flex items-center gap-1 text-xs text-success-fg"><Check aria-hidden className="h-3 w-3" />Ack</span>}
                  {gr.collectionPending && (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-warning-fg"><AlertTriangle aria-hidden className="h-3 w-3" />Collection Pending</span>
                  )}
                </div>
              ))}

              {/* Invoices */}
              {po.invoices.map((inv) => (
                <div key={inv.id} className="flex items-center gap-2 py-1 pl-10 border-l-2 border-primary-200 ml-2">
                  <FileText className="h-3 w-3 text-fg-muted shrink-0" />
                  <Link to={`/invoices/${inv.id}`} className="text-xs text-fg-secondary hover:text-accent-fg">
                    {inv.number}
                  </Link>
                  <StatusBadge status={inv.status} />
                  <span className="amount text-xs text-fg-muted">{formatCAD(inv.amount)}</span>
                </div>
              ))}

              {/* PA */}
              {po.pa && (
                <div className="flex items-center gap-2 py-1 pl-10 border-l-2 border-primary-200 ml-2">
                  <CreditCard className="h-3 w-3 text-fg-muted shrink-0" />
                  {po.pa.number ? (
                    <Link to={`/pa/${po.pa.id}`} className="text-xs text-fg-secondary hover:text-accent-fg">
                      {po.pa.number}
                    </Link>
                  ) : (
                    <span className="text-xs text-fg-muted">PA</span>
                  )}
                  <StatusBadge status={po.pa.status} />
                  {po.pa.locked && po.pa.lockReason && (
                    <span className="text-xs text-fg-muted">— {po.pa.lockReason}</span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface PrPipelineProps {
  items: PrPipelineItem[]
}

export function PrPipeline({ items }: PrPipelineProps) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-fg">My PR Pipeline</h2>
        <div className="flex items-center gap-2">
          <Link to="/pr/new" className="text-xs text-primary-600 hover:underline font-medium">
            + New PR
          </Link>
        </div>
      </div>

      {items.length === 0 && (
        <div className="rounded-xl bg-surface shadow-sm py-10 text-center">
          <p className="text-sm text-fg-muted mb-3">No purchase requisitions yet</p>
          <Link to="/pr/new" className="text-sm text-primary-600 hover:underline">
            Create your first PR →
          </Link>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {items.map((pr) => (
          <PrRow key={pr.id} pr={pr} />
        ))}
      </div>
    </div>
  )
}
