import { ArrowDown, ArrowUp, ChevronDown, EyeOff, ListTree, Lock, LockOpen, Search } from 'lucide-react'
import { DropdownMenu, type MenuItem } from '../primitives/DropdownMenu'
import { TourMark } from '../help'
import type { FieldMeta } from '../resource/listMeta'

export interface ColumnMenuProps {
  field: FieldMeta
  onSearch: () => void
  onSort: (desc: boolean) => void
  /** Absent when grouping is not offered; `canGroup` false when the 3 levels are used. */
  onGroup?: (desc: boolean) => void
  canGroup?: boolean
  onHide: () => void
  /** Locked columns (DataTable `locked`): this column's place among the visible ones (0 = first), how many
   *  are locked now, the cap, and the setter. Absent: no locking from this menu. */
  lock?: { index: number; locked: number; max: number; onLock: (count: number) => void }
}

/** The chevron on a column header: search on it, sort by it, hide it. What a
 *  field cannot do (the server's /meta says so) is left out of the menu. */
export function ColumnMenu({ field, onSearch, onSort, onGroup, canGroup = true, onHide, lock }: ColumnMenuProps) {
  const items: MenuItem[] = [
    ...(field.filterable ? [{ label: 'Search', icon: <Search className="h-3.5 w-3.5" />, onSelect: onSearch, opensDialog: true }] : []),
    ...(field.sortable ? [
      { label: 'Sort ascending', icon: <ArrowUp className="h-3.5 w-3.5" />, onSelect: () => onSort(false), separated: field.filterable },
      { label: 'Sort descending', icon: <ArrowDown className="h-3.5 w-3.5" />, onSelect: () => onSort(true) },
    ] : []),
    ...(field.groupable && onGroup ? [
      { label: 'Group ascending', icon: <ListTree className="h-3.5 w-3.5" />, onSelect: () => onGroup(false), separated: true, disabled: !canGroup },
      { label: 'Group descending', icon: <ListTree className="h-3.5 w-3.5" />, onSelect: () => onGroup(true), disabled: !canGroup },
    ] : []),
    ...(lock ? [
      { label: 'Lock columns up to here', icon: <Lock className="h-3.5 w-3.5" />, onSelect: () => lock.onLock(lock.index + 1), separated: true,
        disabled: lock.index + 1 > lock.max || lock.locked === lock.index + 1,
        disabledReason: lock.index + 1 > lock.max ? `At most ${lock.max} locked columns: move it among the first ${lock.max}`
          : 'Already locked up to here' },
      ...(lock.locked > 0 ? [{ label: 'Unlock columns', icon: <LockOpen className="h-3.5 w-3.5" />, onSelect: () => lock.onLock(0) }] : []),
    ] : []),
    { label: 'Hide column', icon: <EyeOff className="h-3.5 w-3.5" />, onSelect: onHide, separated: true },
  ]
  return (
    <TourMark help="list.sortGroup" slot="list.sortGroup" className="inline-flex">
    <DropdownMenu
      label={`${field.label} column`}
      items={items}
      trigger={
        <button type="button" aria-label={`${field.label} column menu`} className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
          <ChevronDown className="h-3 w-3" />
        </button>
      }
    />
    </TourMark>
  )
}
