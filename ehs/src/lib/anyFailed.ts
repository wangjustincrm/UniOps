/**
 * Did anything this screen asked for fail to arrive?
 *
 * ★ For the shape `query.data ?? []`, which is all over this module. It keeps a
 * page rendering when a request fails — and turns the failure into an empty
 * list, which on most of these screens is a statement: nobody signed it, no
 * treatment was given, no readings were taken. An empty list and a refused
 * request mean opposite things, and one of them is a reason to sign again.
 *
 * It came up as a real defect on permits: signing needed one permission and
 * reading the signatures back needed another, so somebody signed, the write
 * succeeded, the list came back 403, and the screen drew an empty signature
 * list. It was reported as "signatures are not saved".
 *
 * ★ A plain function, not a hook, and named like one. It holds no state and
 * calls nothing — but a `use` prefix makes eslint enforce hook rules on it, so
 * a call placed after an early return became an error in a file where nothing
 * was actually wrong. Worse, the name told the next reader that hook ordering
 * mattered here. It does not.
 *
 * One answer per screen rather than one per list, deliberately: fifty-odd
 * separate warnings would be noise, and noise is how a real one gets missed.
 * What the reader needs is "part of this did not load, so do not read what is
 * missing as an absence".
 */
import type { UseQueryResult } from '@tanstack/react-query'

export function anyFailed(
  ...queries: Pick<UseQueryResult<unknown>, 'isError'>[]
): boolean {
  return queries.some((q) => q.isError)
}
