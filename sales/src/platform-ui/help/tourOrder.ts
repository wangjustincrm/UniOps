// Every tour stop's place, by slot name (the user, 2026-11-17: each part declares its place, like a
// z-index, managed in one place): lower first. A component names its slot, never a number, so two can't
// collide by accident; a module may move a slot through TourManager's `slots` (a layer, like texts).

export const DEFAULT_SLOTS: Record<string, number> = {
  // a page with a list: the view, then finding, then the table, then refreshing and exporting
  'list.views': 10, 'list.search': 20, 'list.phone': 25, 'list.searchPanel': 30, 'row.status': 35,
  'list.columns': 40, 'list.refresh': 60, 'list.export': 70,
  // a record's sheet: what it is, what to do, its figures, its actions, its sections (its status badge: row.status)
  'record.header': 10, 'record.next': 20, 'record.facts': 30, 'record.actions': 40, 'record.tabs': 50,
}
