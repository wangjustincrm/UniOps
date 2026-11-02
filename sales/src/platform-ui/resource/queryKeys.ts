// Query keys derived from the resource (its path), never written by hand:
// a list, a record and its history refresh together whoever changed them.

interface Keyed { basePath: string }

export const queryKeys = {
  /** Every page / filter / sort of a list. */
  list: (r: Keyed) => ['platform-list', r.basePath] as const,
  /** One record (a detail view). */
  detail: (r: Keyed, id: number | string) => ['platform-detail', r.basePath, id] as const,
  /** One record's audit history. */
  audit: (r: Keyed, id: number | string) => ['platform-audit', r.basePath, id] as const,
}
