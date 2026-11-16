import type { ColumnDef, RowData } from '@tanstack/react-table'
import type { HelpId } from '../help'

// What a column says about itself, declared with the column (2026-11-10), so every table that
// shows it reads the same thing: LocalTable and QueryTable / ListPage.
declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Starts hidden: the eye in the header shows it (a saved view can too). */
    hidden?: boolean
    /** In the card view (a narrow table: a phone, a narrow sheet): the card's title, a badge
     *  at its top right, or left out. Unset: a "label: value" line (the first such column is
     *  the title when no column claims it). */
    card?: 'title' | 'badge' | 'hide'
    /** A help id: a ? beside the header (and the card's label) that says what the column holds. */
    help?: HelpId
  }
}

export const columnId = <T,>(c: ColumnDef<T, unknown>): string =>
  c.id ?? (c as { accessorKey?: string }).accessorKey ?? ''

/** The column, starting hidden: `startHidden(col.text('sscc', 'SSCC'))`. */
export const startHidden = <T,>(c: ColumnDef<T, unknown>): ColumnDef<T, unknown> => ({ ...c, meta: { ...c.meta, hidden: true } })

/** The ids that start hidden: the columns that say so, and `extra` (a page's choice that depends on its data). */
export const startsHidden = <T,>(columns: ColumnDef<T, unknown>[], extra: readonly string[] = []): string[] =>
  [...new Set([...columns.filter((c) => c.meta?.hidden).map(columnId), ...extra])]

/** The column, in the card view as `card` (title / badge / hide): `onCard(col.text('ref', 'Ref'), 'title')`. */
export const onCard = <T,>(c: ColumnDef<T, unknown>, card: 'title' | 'badge' | 'hide'): ColumnDef<T, unknown> => ({ ...c, meta: { ...c.meta, card } })

/** The column, with a ? beside its header saying what it holds: `withHelp(col.number('on_hand_ea', 'On hand'), 'term:On hand')`. */
export const withHelp = <T,>(c: ColumnDef<T, unknown>, help: HelpId): ColumnDef<T, unknown> => ({ ...c, meta: { ...c.meta, help } })
