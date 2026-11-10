/**
 * Where an action's source document lives.
 *
 * An action says what raised it — "from INC-2026-0041" — and that was plain
 * grey text, so the one question the page reliably provokes ("what happened,
 * then?") ended with somebody searching the register by hand.
 *
 * ★ Null where there is no page that shows that record. A link to a list is
 * not a link to the record: it lands somebody on a screen of a hundred rows
 * with no indication which one they were sent for, which is worse than being
 * told there is nowhere to go. Where the register can be narrowed to the one
 * row, the reference is passed as its search term instead.
 */
export function sourceLink(
  sourceType: string | null | undefined,
  sourceId: string | null | undefined,
  sourceRef?: string | null,
): string | null {
  if (!sourceId) return null
  switch (sourceType) {
    case 'incident': return `/incidents/${sourceId}`
    case 'inspection': return `/forms/submissions/${sourceId}`
    case 'permit': return `/permits/${sourceId}`
    case 'jhsc': return `/jhsc/meetings/${sourceId}`
    // Round 10: a safety equipment check recorded without a checklist.
    case 'asset': return `/equipment?asset=${sourceId}`
    // Round 12, HAZ-04: a hazard has its own page now.
    case 'hazard': return `/hazards/${sourceId}`
    // A drill has a register but no page of its own. The reference is what the
    // register searches by, so it arrives filtered to the one row.
    case 'drill': return sourceRef ? `/emergency?q=${encodeURIComponent(sourceRef)}` : null
    default: return null
  }
}

/** What to call the source when the action never stored its number. */
export const SOURCE_NOUN: Record<string, string> = {
  incident: 'the incident',
  inspection: 'the inspection',
  permit: 'the permit',
  jhsc: 'the JHSC meeting',
  hazard: 'the hazard',
  drill: 'the drill',
  asset: 'the safety equipment',
  audit: 'the audit',
  observation: 'the observation',
}
