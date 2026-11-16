import type { GrLineCondition } from '@/services/gr'

// ─── Notes the GR pages write into collection_notes ───────────────────────────
//
// The GR has one free-text collection_notes field (shown on GR Detail as
// Acceptance Notes / Collection Notes). Two pages put more than the user's own
// notes into it, because the PRD stores nothing separately for them.

// Confirm Service Completion → Has Issues: "<acceptance notes> — Issues noted:
// <details>". The marker is how GR Detail tells that the service had issues
// (ISS-0179).
const ISSUES_MARKER = ' — Issues noted: '

export function serviceAcceptanceNotes(acceptanceNotes: string, issueDetails: string | null): string {
  const notes = acceptanceNotes.trim()
  return issueDetails ? `${notes}${ISSUES_MARKER}${issueDetails.trim()}` : notes
}

export function serviceHasIssues(gr: { gr_type: string; collection_notes?: string | null }): boolean {
  return gr.gr_type === 'service' && !!gr.collection_notes?.includes(ISSUES_MARKER)
}

// Collect Goods: the per-line conditions are not stored separately (GR-C-002),
// so each line marked other than Good — or marked Good where the GR recorded
// it damaged / with a discrepancy — is added to the collection notes after
// the General Notes, as "Line conditions at collection: <item>: Discrepancy — <details>; …"
// (ISS-0185). Nobody is notified and no task is raised.
const CONDITION_LABELS: Record<GrLineCondition, string> = {
  good: 'Good',
  discrepancy: 'Discrepancy',
  damaged: 'Damaged',
}

export interface CollectedLine {
  description: string
  condition: GrLineCondition
  notes: string
  /** What the GR recorded for the line on receipt. */
  recordedCondition: GrLineCondition
}

export function collectionNotesWithLines(generalNotes: string, lines: CollectedLine[]): string | undefined {
  const marked = lines
    .filter((l) => l.condition !== 'good' || l.condition !== l.recordedCondition)
    .map((l) => {
      const notes = l.condition !== 'good' ? l.notes.trim() : ''
      return `${l.description}: ${CONDITION_LABELS[l.condition]}${notes ? ` — ${notes}` : ''}`
    })
  const parts = [
    generalNotes.trim(),
    marked.length ? `Line conditions at collection: ${marked.join('; ')}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}
