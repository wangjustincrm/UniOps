/**
 * Which consequence dimensions the matrix can actually describe.
 *
 * HSE's matrix scores severity five ways — people, unplanned downtime,
 * reputation, environmental, asset damage — and the whole point of recording
 * which one was used is that "severity 3" means different things under each.
 * Two of the five arrived with no wording at all: every band under
 * Environmental and Asset Damage is empty, so choosing one of them offered a
 * bare number with a dash beside it. The second acceptance round asked the
 * obvious question back: "is 2 bad or good?"
 *
 * A band with nothing written against it cannot be scored consistently by two
 * people, so it is not offered. The wording exists in HSE's own matrix
 * document and needs to be loaded before these come back — deliberately not
 * transcribed here, because the two columns interleave in the source and a
 * band given the wrong description would push real work up or down the
 * priority order without anybody noticing.
 */
/**
 * Deliberately `unknown`: each screen declares its own Severity interface, and
 * an interface has no implicit index signature, so a named type would force
 * every caller to restate the same shape. All this needs is one field looked
 * up by a key that is only known at runtime.
 */
export function describedConsequenceTypes(
  types: string[] | undefined,
  severity: readonly unknown[] | undefined,
): string[] {
  if (!types || !severity?.length) return types ?? []
  const described = types.filter((t) =>
    severity.some((band) => {
      const text = (band as Record<string, unknown>)[t]
      return typeof text === 'string' && text.trim() !== ''
    }))
  // Never leave nothing selectable: if the matrix is empty across the board,
  // that is a configuration problem to show, not one to hide by offering an
  // empty dropdown.
  return described.length ? described : types
}

/** Human wording for a dimension key, since they are stored snake_case. */
export function consequenceLabel(key: string): string {
  const words = key.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}
