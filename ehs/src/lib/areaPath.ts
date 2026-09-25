/**
 * A location path, written the way people say it.
 *
 * ★ Three screens did `path.split('/').slice(1).join(' › ')`, dropping the
 * first segment. That assumes every path hangs off one root, and this tree has
 * no such thing: its top level is "105 – Warehouse", "106 – Utilities", "107 –
 * Office", "Outside the Plant" and "Production". So a machine in the warehouse
 * showed a blank area — and blank, not "No area", because the path was there
 * and simply rendered to nothing. Anything under Production lost the word
 * "Production".
 *
 * Every segment, separated. If a root ever needs hiding it should be hidden by
 * name, not by position.
 */
export function formatAreaPath(path: string | null | undefined): string | null {
  if (!path) return null
  const parts = path.split('/').map((p) => p.trim()).filter(Boolean)
  return parts.length ? parts.join(' › ') : null
}
