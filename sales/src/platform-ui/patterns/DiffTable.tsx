import type { ColumnDef } from '../primitives/DataTable'
import { LocalTable } from '../primitives/LocalTable'

export interface DiffRow { left: string | null; right: string | null; same: boolean }

/** Two versions side by side, one segment / line per row; a differing row is
 *  highlighted and carries data-differs (e.g. a file received against the one
 *  that would be built). A missing side shows "—". */
export function DiffTable({ rows, leftLabel, rightLabel, ariaLabel }: {
  rows: DiffRow[]; leftLabel: string; rightLabel: string; ariaLabel: string
}) {
  const columns: ColumnDef<DiffRow, unknown>[] = [
    { id: 'left', header: leftLabel, cell: ({ row }) => <span className="break-all font-mono">{row.original.left ?? '—'}</span> },
    { id: 'right', header: rightLabel, cell: ({ row }) => <span className="break-all font-mono">{row.original.right ?? '—'}</span> },
  ]
  return (
    <LocalTable<DiffRow>
      columns={columns}
      rows={rows}
      rowKey={(_, i) => i}
      ariaLabel={ariaLabel}
      emptyTitle="Nothing to compare"
      rowAttrs={(r) => (r.same ? {} : { className: 'bg-warning-50 text-warning-700', 'data-differs': true })}
    />
  )
}
