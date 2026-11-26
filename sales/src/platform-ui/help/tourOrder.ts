// Every tour stop, by slot name (the user, 2026-11-17: managed in one place, each part names its place
// like a z-index): its place (lower first), its group in the help drawer and the tips catalog, and its
// version (raise `v` after a change that matters: the stop is new again for everyone). A slot with no
// element on a page (e.g. how to sort) is still a tip: listed in the drawer and the catalog, without Show. A module may move,
// regroup or re-version a slot through TourManager's `slots` (a layer, like texts).

export interface SlotDef {
  order: number
  group: string
  /** false: a tip, not a tour stop (the user's choice, 2026-11-17: tours stay short, ~5 stops; the rest are
   *  tips in the drawer, with Show where they are on the page). */
  tour?: boolean
  /** Its version (default 1): a viewer who saw an older one sees it as new. */
  v?: number
}

/** The groups, in the drawer's order; a `shared` one (the same on every list or record) folds away once seen. */
export const GROUPS: Record<string, { label: string; shared?: boolean }> = {
  page: { label: 'On this page' },
  lists: { label: 'Lists (the same on every list)', shared: true },
  records: { label: 'Records (the same on every record)', shared: true },
}

export const DEFAULT_SLOTS: Record<string, SlotDef> = {
  // a page with a list: the view, then finding, then the table, then refreshing and exporting
  'list.views': { order: 10, group: 'lists' }, 'list.defaultView': { order: 12, group: 'lists' }, 'list.saveView': { order: 15, group: 'lists' },
  'list.search': { order: 20, group: 'lists' }, 'list.statusFind': { order: 22, group: 'lists', v: 2 }, 'list.phone': { order: 25, group: 'lists' },
  // #32 (2026-11-26): the search on the right, a column's own search: new again for everyone (v 2)
  'list.searchPanel': { order: 30, group: 'lists', tour: false, v: 2 }, 'list.columnSearch': { order: 31, group: 'lists', tour: false },
  'list.chips': { order: 32, group: 'lists', tour: false, v: 2 },
  'row.status': { order: 35, group: 'page' }, 'list.columns': { order: 38, group: 'lists', tour: false },
  'list.rowOpen': { order: 40, group: 'lists' }, 'list.sortGroup': { order: 45, group: 'lists' },
  'list.rowDetails': { order: 50, group: 'lists', tour: false }, 'list.groups': { order: 52, group: 'lists', tour: false },
  'list.totals': { order: 54, group: 'lists', tour: false }, 'list.moreColumns': { order: 56, group: 'lists', tour: false },
  'list.refresh': { order: 60, group: 'lists', tour: false }, 'list.export': { order: 70, group: 'lists', tour: false },
  'list.select': { order: 36, group: 'lists', tour: false },
  'list.pages': { order: 80, group: 'lists', tour: false }, 'list.notices': { order: 85, group: 'lists', tour: false },
  // a record's sheet: what it is, what to do, its figures, its actions, its sections (its status badge: row.status)
  'record.header': { order: 10, group: 'records' }, 'record.next': { order: 20, group: 'page' }, 'record.facts': { order: 30, group: 'records' },
  'record.actions': { order: 40, group: 'records' }, 'record.tabs': { order: 50, group: 'records' },
}
