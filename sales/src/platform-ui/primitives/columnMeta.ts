import type { ColumnDef, RowData } from '@tanstack/react-table'

// What a column says about itself, declared with the column (2026-11-10), so every table that
// shows it reads the same thing: LocalTable and QueryTable / ListPage.
declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Starts hidden: the eye in the header shows it (a saved view can too). */
    hidden?: boolean
  }
}

export const columnId = <T,>(c: ColumnDef<T, unknown>): string =>
  c.id ?? (c as { accessorKey?: string }).accessorKey ?? ''

/** The column, starting hidden: `startHidden(col.text('sscc', 'SSCC'))`. */
export const startHidden = <T,>(c: ColumnDef<T, unknown>): ColumnDef<T, unknown> => ({ ...c, meta: { ...c.meta, hidden: true } })

/** The ids that start hidden: the columns that say so, and `extra` (a page's choice that depends on its data). */
export const startsHidden = <T,>(columns: ColumnDef<T, unknown>[], extra: readonly string[] = []): string[] =>
  [...new Set([...columns.filter((c) => c.meta?.hidden).map(columnId), ...extra])]
