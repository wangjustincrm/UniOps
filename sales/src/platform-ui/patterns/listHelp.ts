import type { HelpEntry } from '../help'

// The list tools' own help (the user, 2026-11-17: written once, every list gets it). Each tool puts its
// data-help on itself (a tour can point at it); ListPage lists the tools a list really has under "Using
// lists" in its page's help panel. Generic words only: a module adds or overrides texts in its own layer.

declare module '../help/types' {
  interface HelpRegistry {
    [tool: `list.${string}`]: true
    [part: `record.${string}`]: true
  }
}

export const PLATFORM_HELP: Record<string, HelpEntry> = {
  'list.search': { title: 'Search', body: 'Type in Search and press Enter.' },
  'list.searchPanel': { title: 'Search on any field', body: 'The sliders button (Search on any field) finds by one column: a date range, a status, a partner.', buttons: ['Search on any field'] },
  'list.views': { title: 'Views', body: 'The View button (beside Search) switches between saved filters and columns. System views come with the page; My views are yours.' },
  'list.defaultView': { title: 'Default view', body: 'In the View list, a view of yours → Set as default: the page opens on it.', buttons: ['Set as default'] },
  'list.saveView': { title: 'Save a view', body: 'Change filters, sort or columns, then Save view (keeps it) or Save as a new view. Reset to the saved view drops your changes.',
    buttons: ['Save view', 'Save as a new view', 'Reset to the saved view'] },
  'list.columns': { title: 'Columns', body: "The eye button (the column chooser): show, hide or move columns. Under More fields is every other field of the rows: tick one to add it as a column; Find a field narrows the list. The box at the top (and each group's) shows or hides them all; a dash means some are shown. Drag a header's grip to move it. Save the view to keep your columns." },
  'list.sortGroup': { title: 'Column menu', body: "A header's arrow menu: Search (that column), Sort ascending / descending, Group ascending / descending, Hide column. Click a header to sort by it.",
    buttons: ['Search', 'Sort ascending', 'Group ascending', 'Hide column'] },
  'list.statusFind': { title: 'Find by status', body: "To see only some statuses: Search on any field → Status, tick the ones you want; or a status column's arrow menu → Search. A view (e.g. Open, On hold) already does this for you.",
    buttons: ['Search on any field', 'Search'] },
  'list.chips': { title: 'Active filters', body: 'The chips show everything that narrows the list (filters, search, sort, grouping). × removes one; Reset to the saved view removes your changes.',
    buttons: ['Reset to the saved view'] },
  'list.rowOpen': { title: 'Open a row', body: 'Click a row (or press Enter) to open it. In the record, J / K (or Alt+↑ / Alt+↓) move to the next or previous one.' },
  'list.rowDetails': { title: 'Row details', body: "▸ opens a row's details under it (e.g. an item's lots) without leaving the list." },
  'list.groups': { title: 'Groups', body: "Rows grouped by a field; a group's header folds it, its totals sit beside it. To group, drag a header's grip onto the Grouping row that appears (or use the column menu); drag a Grouping chip onto another to change the order, or onto Drop here to remove (× works too). At most 3." },
  'list.totals': { title: 'Totals', body: 'The totals of the whole result (every page), per currency.' },
  'list.pages': { title: 'Pages', body: 'The rows count and pages; Per page sets how many rows a page shows.' },
  'list.notices': { title: 'Notices', body: "What happened on this list (a refresh, an export, an error); the bell keeps the session's last 20." },
  'list.moreColumns': { title: 'More columns', body: 'The table is wider than the screen: More columns → scrolls to the rest.', buttons: ['More columns →'] },
  'list.refresh': { title: 'Refresh', body: 'The refresh button reads the list again; its arrow sets Auto-refresh (30s, 1m, 5m).', buttons: ['Auto-refresh'] },
  'list.export': { title: 'Export CSV', body: 'Export CSV exports every row the current filters select, not only this page. Visible columns: the columns you see, in their order and with their names (an amount with its currency). All fields: the list\'s full export.', buttons: ['Export CSV'] },
  'row.status': { title: 'Status', body: "Each row's status: tap it to read what it means and what to do next." },
  // A record's parts (RecordSheet marks them): its own tour, "Show me around" in its header.
  'record.header': { title: 'The record', body: "Its name, its status and what it is. The arrows go to the list's previous or next record; Refresh reads it again." },
  'record.next': { title: 'Next step', body: 'What to do now, and whose turn it is.' },
  'record.facts': { title: 'Key figures', body: 'The figures that matter for this record, at a glance.' },
  'record.actions': { title: 'Actions', body: 'What you can do with it now; More holds the rest. A greyed button says why when you tap it.' },
  'record.tabs': { title: 'Sections', body: 'Its details, one tab each; a dot marks a tab that needs a look.' },
  'list.phone': { title: 'On a phone', body: 'Each row is a card: Sort is a menu, "N more fields" shows the rest of a card, the arrows at the bottom change page.',
    buttons: ['more fields'] },
}

/** The tools a list has, in the order "Using lists" explains them. */
export function listTools(has: { search: boolean; panel: boolean; views: boolean; export: boolean }): string[] {
  return [
    ...(has.search ? ['list.search'] : []),
    ...(has.panel ? ['list.statusFind', 'list.searchPanel'] : []),
    ...(has.views ? ['list.views', 'list.defaultView', 'list.saveView'] : []),
    'list.columns', 'list.sortGroup', 'list.refresh',
    ...(has.export ? ['list.export'] : []),
    'list.phone',
  ]
}
