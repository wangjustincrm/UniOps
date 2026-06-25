/** Print-and-check-in page (PRD §2.2 / §2.3 / VMS-LB-007).
 *
 * Flow when the user lands here from VisitDetailPage:
 *   1. Fetch visit + visitor + host.
 *   2. Render the badge (BadgePreview) full-page.
 *   3. POST /print-badge → atomic check-in on backend.
 *   4. On success, trigger window.print() so the browser print dialog opens.
 *   5. Provide a "Back to visit" button.
 *
 * Reprints reach here through `?reason=<text>` query param — passed straight
 * through to the API.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AlertCircle, ArrowLeft, Loader2, Printer } from 'lucide-react'
import {
  useVisit, useVisitor, useUserBrief, usePrintBadge, useBadgeConfig,
} from '@/services/api'
import { BadgePreview } from '@/components/BadgePreview'

export default function BadgePrintPage() {
  const { visitId } = useParams<{ visitId: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const reprintReason = searchParams.get('reason') || null

  const { data: visit, isLoading: vLoading, error } = useVisit(visitId)
  const { data: visitor }                            = useVisitor(visit?.visitor_id)
  const { data: host }                               = useUserBrief(visit?.host_id)
  const print                                        = usePrintBadge(visitId)
  const { data: badgeConfig } = useBadgeConfig()

  // Track whether we've already called print-badge for this page visit.
  // (Without this guard, React 19's StrictMode dev double-mount would
  //  trigger the API twice and create a spurious reprint row.)
  const printCalled = useRef(false)
  const [readyToPrint, setReadyToPrint] = useState(false)

  // Stable references for the mutation API so the trigger effect's deps don't
  // include the whole `print` object (which is a new reference every render
  // and was retriggering the effect after each mutation state change).
  const mutate = print.mutate

  // Phase 1: once visit + visitor + host are loaded → call the API.
  useEffect(() => {
    if (!visit || !visitor || printCalled.current) return
    printCalled.current = true
    mutate(
      { template_used: 'standard', reprint_reason: reprintReason },
      { onSuccess: () => setReadyToPrint(true) },
    )
  }, [visit, visitor, reprintReason, mutate])

  // Phase 2: once the API write has succeeded → open the browser print dialog.
  useEffect(() => {
    if (!readyToPrint) return
    // Tiny defer so the DOM paints the badge layout before the dialog opens.
    const t = setTimeout(() => window.print(), 100)
    return () => clearTimeout(t)
  }, [readyToPrint])

  const loading = vLoading || (print.isPending && !print.error)
  const additionalVisitors = visit?.additional_visitors ?? []

  return (
    <div className="badge-print-root">
      {/* Toolbar (hidden in print output) */}
      <div className="no-print mb-4 flex items-center justify-between gap-3">
        <button
          onClick={() => navigate(`/${visitId}`)}
          className="inline-flex items-center gap-1.5 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to visit
        </button>
        {/* Manual print is always available once the badge layout has rendered
            — even if the check-in API is slow or stuck, the operator can still
            print the badge. */}
        {visit && visitor && (
          <button
            onClick={() => window.print()}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700"
          >
            <Printer className="h-4 w-4" />
            {readyToPrint ? 'Print again' : 'Print now'}
          </button>
        )}
      </div>

      {/* Status banner (also hidden in print output) */}
      {print.error && (
        <div className="no-print mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Could not record this badge print.</p>
            <p className="text-xs">{print.error.message}</p>
          </div>
        </div>
      )}
      {loading && (
        <div className="no-print mb-4 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          {vLoading ? 'Loading visit…' : 'Recording check-in…'}
        </div>
      )}
      {error && (
        <div className="no-print mb-4 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
          {error.message}
        </div>
      )}

      {/* Two identical copies per visitor, side-by-side in a row — cut the pair
          out and fold along the centre to cover both faces of the badge holder.
          Each visitor's pair gets its own page (see the @media print block). */}
      {visit && visitor && (
        <>
          {[visitor, ...additionalVisitors].map((v) => (
            <div key={v.id} className="badge-pair">
              <BadgePreview visit={visit} visitor={v} host={host ?? null} config={badgeConfig} />
              <BadgePreview visit={visit} visitor={v} host={host ?? null} config={badgeConfig} />
            </div>
          ))}
        </>
      )}

      {/* Print layout: landscape, two copies per row, one visitor pair per page.
          Badges stay full size (140mm); the pair is wider than the sheet, so
          the operator prints at ~60% scale to fit both in a row. */}
      <style>{`
        .badge-pair {
          display: flex;
          gap: 4mm;
          justify-content: center;
          align-items: flex-start;
          margin-bottom: 8mm;
        }
        .badge-pair > .badge-card { flex: 0 0 auto; }
        @media print {
          /* Landscape Letter via explicit swapped dimensions — the
             "letter landscape" keyword is ignored by some Chrome/Edge builds. */
          @page { size: 11in 8.5in; margin: 1cm; }
          body { background: white !important; }
          .no-print { display: none !important; }
          .badge-pair {
            margin-bottom: 0;
            page-break-after: always;
            page-break-inside: avoid;
            break-inside: avoid;
          }
          .badge-pair:last-child { page-break-after: auto; }
        }
      `}</style>
    </div>
  )
}
