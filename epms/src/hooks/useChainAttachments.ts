/**
 * useChainAttachments — resolve a PA's DIRECT document lineage (PR, PO, GR, INV,
 * PA) and aggregate every document's attachments into one grouped model.
 *
 * Lineage (see spec §2): PA → its invoices (pa.invoice_ids) → those invoices'
 * GRs (invoice.gr_ids, de-duplicated) → PA's PO (pa.po_id) → PO's PR (po.pr_id).
 * A prepayment PA with no invoices collapses to PA + PO + PR.
 *
 * Attachment sources differ by service:
 *   PR/PO/GR/PA → epms-api  /{type}/{id}/attachments        (+ /{attId}/download)
 *   INV         → expense-api /invoice-attachments?...       (+ /{attId}/file)
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth.store'
import { EXPENSE_BASE } from '@/lib/api'
import { usePa } from '@/hooks/usePas'
import { usePo } from '@/hooks/usePos'
import { usePr } from '@/hooks/usePrs'
import { poAttachmentService } from '@/services/poAttachments'
import { prAttachmentService } from '@/services/prAttachments'
import { grAttachmentService } from '@/services/grAttachments'
import { paAttachmentService } from '@/services/paAttachments'
import { invoiceService } from '@/services/invoices'
import { grService } from '@/services/gr'

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'

export interface ChainAttachment {
  id: string
  filename: string
  contentType: string
  sizeBytes: number
  fetchUrl: string
  printable: boolean
}
export interface ChainDocGroup {
  docType: 'PR' | 'PO' | 'GR' | 'INV' | 'PA'
  docNumber: string
  docId: string
  attachments: ChainAttachment[]
}

function isPrintable(ct: string): boolean {
  return ct === 'application/pdf' || ct.startsWith('image/')
}

/** epms-api attachment meta → ChainAttachment. `seg` is the URL path segment. */
function mapEpmsAtt(
  seg: 'pr' | 'po' | 'gr' | 'pa',
  docId: string,
  a: { id: string; filename: string; content_type: string; file_size: number },
): ChainAttachment {
  return {
    id: a.id,
    filename: a.filename,
    contentType: a.content_type,
    sizeBytes: a.file_size,
    fetchUrl: `${API_BASE}/${seg}/${docId}/attachments/${a.id}/download`,
    printable: isPrintable(a.content_type),
  }
}

export function useChainAttachments(paId: string) {
  const token = useAuthStore((s) => s.token)

  const { data: pa, isLoading: paLoading, isError: paError } = usePa(paId)
  const poId = pa?.po_id ?? ''
  const invoiceIds = useMemo(() => pa?.invoice_ids ?? [], [pa])

  const { data: po, isLoading: poLoading, isError: poError } = usePo(poId)
  const prId = po?.pr_id ?? ''
  const { data: pr, isLoading: prLoading, isError: prError } = usePr(prId)

  // Invoices (each invoice → its detail, so we can read gr_ids + number)
  const invoiceQueries = useQueries({
    queries: invoiceIds.map((id) => ({
      queryKey: ['invoices', id],
      queryFn: () => invoiceService.get(id),
      enabled: !!id,
      staleTime: 30_000,
    })),
  })
  const invoices = invoiceQueries.map((q) => q.data).filter(Boolean) as NonNullable<
    (typeof invoiceQueries)[number]['data']
  >[]

  // GR ids: union of gr_ids across the resolved invoices, de-duplicated.
  const grIds = useMemo(() => {
    const s = new Set<string>()
    for (const inv of invoices) for (const g of inv.gr_ids ?? []) s.add(g)
    return [...s]
  }, [invoices])

  const grQueries = useQueries({
    queries: grIds.map((id) => ({
      queryKey: ['gr', id],
      queryFn: () => grService.get(id),
      enabled: !!id,
      staleTime: 30_000,
    })),
  })
  const grs = grQueries.map((q) => q.data).filter(Boolean) as NonNullable<
    (typeof grQueries)[number]['data']
  >[]

  // ── Attachment-list queries, one per resolved doc ──────────────────────────
  // (Built via .map() over a 0-or-1-element array — not a ternary tuple literal —
  // so useQueries' overload resolution sees a uniform array type instead of a
  // `[] | [X]` union it can't reconcile.)
  const prAtt = useQueries({
    queries: (pr ? [pr] : []).map((p) => ({ queryKey: ['pr-att', p.id], queryFn: () => prAttachmentService.list(p.id), staleTime: 30_000 })),
  })
  const poAtt = useQueries({
    queries: (po ? [po] : []).map((p) => ({ queryKey: ['po-att', p.id], queryFn: () => poAttachmentService.list(p.id), staleTime: 30_000 })),
  })
  const paAtt = useQueries({
    queries: (pa ? [pa] : []).map((p) => ({ queryKey: ['pa-att', p.id], queryFn: () => paAttachmentService.list(p.id), staleTime: 30_000 })),
  })
  const grAtt = useQueries({
    queries: grs.map((g) => ({ queryKey: ['gr-att', g.id], queryFn: () => grAttachmentService.list(g.id), staleTime: 30_000 })),
  })
  const invAtt = useQueries({
    queries: invoices.map((inv) => ({
      queryKey: ['inv-att', inv.id],
      queryFn: async (): Promise<ChainAttachment[]> => {
        const res = await fetch(
          `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${inv.id}&invoice_source=epms`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        )
        if (!res.ok) return []
        const raw = (await res.json()) as Array<{
          id: string; file_name: string; content_type: string; file_size_bytes: number
        }>
        return raw.map((a) => ({
          id: a.id,
          filename: a.file_name,
          contentType: a.content_type,
          sizeBytes: a.file_size_bytes,
          fetchUrl: `${EXPENSE_BASE}/api/v1/invoice-attachments/${a.id}/file`,
          printable: isPrintable(a.content_type),
        }))
      },
      staleTime: 30_000,
    })),
  })

  // ── Assemble grouped model in paper-trail order: PR, PO, GR(s), INV(s), PA ──
  // Single identity key built from actual doc/attachment ids — reflects add/
  // delete/reassign correctly (unlike stringifying data objects, which only
  // detects count changes), and satisfies the react-hooks/use-memo "simple
  // expression" rule with one dependency.
  const groupsKey = [
    pr?.id, po?.id, pa?.id,
    grs.map((g) => g.id).join(','),
    invoices.map((i) => i.id).join(','),
    (prAtt[0]?.data ?? []).map((a) => a.id).join(','),
    (poAtt[0]?.data ?? []).map((a) => a.id).join(','),
    (paAtt[0]?.data ?? []).map((a) => a.id).join(','),
    grAtt.map((q) => (q.data ?? []).map((a) => a.id).join('|')).join(','),
    invAtt.map((q) => (q.data ?? []).map((a) => a.id).join('|')).join(','),
  ].join(';')

  const groups = useMemo<ChainDocGroup[]>(() => {
    const out: ChainDocGroup[] = []
    if (pr) out.push({ docType: 'PR', docNumber: pr.number, docId: pr.id, attachments: (prAtt[0]?.data ?? []).map((a) => mapEpmsAtt('pr', pr.id, a)) })
    if (po) out.push({ docType: 'PO', docNumber: po.number, docId: po.id, attachments: (poAtt[0]?.data ?? []).map((a) => mapEpmsAtt('po', po.id, a)) })
    grs.forEach((g, i) => out.push({ docType: 'GR', docNumber: g.number, docId: g.id, attachments: (grAtt[i]?.data ?? []).map((a) => mapEpmsAtt('gr', g.id, a)) }))
    invoices.forEach((inv, i) => out.push({ docType: 'INV', docNumber: inv.internal_ref, docId: inv.id, attachments: invAtt[i]?.data ?? [] }))
    if (pa) out.push({ docType: 'PA', docNumber: pa.pa_number, docId: pa.id, attachments: (paAtt[0]?.data ?? []).map((a) => mapEpmsAtt('pa', pa.id, a)) })
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupsKey])

  // `groups` is intentionally keyed off `groupsKey` above rather than its raw
  // closed-over inputs, so the compiler can't verify this downstream memo either.
  // eslint-disable-next-line react-hooks/preserve-manual-memoization
  const total = useMemo(() => groups.reduce((n, g) => n + g.attachments.length, 0), [groups])

  const isLoading =
    paLoading || poLoading || prLoading ||
    invoiceQueries.some((q) => q.isLoading) || grQueries.some((q) => q.isLoading) ||
    prAtt.some((q) => q.isLoading) || poAtt.some((q) => q.isLoading) ||
    paAtt.some((q) => q.isLoading) || grAtt.some((q) => q.isLoading) ||
    invAtt.some((q) => q.isLoading)

  const error =
    paError || poError || prError ||
    invoiceQueries.some((q) => q.isError) || grQueries.some((q) => q.isError) ||
    prAtt.some((q) => q.isError) || poAtt.some((q) => q.isError) ||
    paAtt.some((q) => q.isError) || grAtt.some((q) => q.isError) ||
    invAtt.some((q) => q.isError)

  return { groups, total, isLoading, error }
}
