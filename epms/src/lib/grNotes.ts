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
