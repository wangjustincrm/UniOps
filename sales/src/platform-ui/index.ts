// The module's UI library (design §13.15). Three layers, one way:
//   primitives/  visual only — imports primitives, lib and @uniops/shell
//   resource/    talking to the API — ResourceClient, querySpec, businessError
//   patterns/    composes primitives and resource — permissions, forms, tables
// Pages and features import from '@/platform-ui' only.

export { cn } from './lib/cn'

// ── primitives ───────────────────────────────────────────────────────────────
export {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, FormField, Input, Label, Pagination, Skeleton, SkeletonRow,
} from './primitives/shell'
export type {
  BadgeProps, ButtonProps, CardContentProps, CardHeaderProps, CardProps, CardTitleProps, FormFieldProps, InputProps,
  LabelProps, PaginationProps, SkeletonProps, SkeletonRowProps,
} from './primitives/shell'
export { Dialog } from './primitives/Dialog'
export { Sheet } from './primitives/Sheet'
export { Popover } from './primitives/Popover'
export { ToastProvider } from './primitives/Toast'
export { useToast } from './primitives/toastContext'
export type { ToastInput, ToastVariant } from './primitives/toastContext'
export { Tabs } from './primitives/Tabs'
export type { TabItem } from './primitives/Tabs'
export { Combobox } from './primitives/Combobox'
export { DropdownMenu } from './primitives/DropdownMenu'
export type { MenuItem, DropdownMenuProps } from './primitives/DropdownMenu'
export { Chip } from './primitives/Chip'
export type { ChipProps } from './primitives/Chip'
export { TagInput } from './primitives/TagInput'
export { FilePicker } from './primitives/FilePicker'
export type { FilePickerProps } from './primitives/FilePicker'
export { Select } from './primitives/Select'
export type { SelectOption, SelectProps } from './primitives/Select'
export { Checkbox, CheckboxList } from './primitives/Checkbox'
export type { CheckboxProps, CheckboxListProps } from './primitives/Checkbox'
export { CheckboxList as MultiSelect } from './primitives/Checkbox'
export { Autocomplete } from './primitives/Autocomplete'
export type { AutocompleteProps } from './primitives/Autocomplete'
export { NumberInput, DateInput, DateTimeInput, Textarea } from './primitives/FieldInputs'
export type { DateTimeInputProps, TextareaProps } from './primitives/FieldInputs'
export { isDateOnly, isoToLocalInput, localInputToIso } from './primitives/dateValues'
export type { ComboOption } from './primitives/Combobox'
export { DataTable } from './primitives/DataTable'
export type { ColumnDef, DataTableProps, SortValue } from './primitives/DataTable'
export { StatusBadge } from './primitives/StatusBadge'
export type { StatusStyle, BadgeVariant } from './primitives/StatusBadge'
export { EmptyState, ErrorState, ErrorLine } from './primitives/States'

// ── resource ─────────────────────────────────────────────────────────────────
export {
  toSearchParams, defaultSpec, MAX_PAGE_SIZE, clampPageSize, getFilter, setFilter, clearFilter, filtersOf,
  setFieldFilters, removeFilterAt, sortBy, flipSort, removeSort, escapeValue,
} from './resource/querySpec'
export type { QuerySpec, SortKey, Filter, FilterOp, FilterValue, Page } from './resource/querySpec'
export { useListMeta } from './resource/listMeta'
export type { FieldMeta, FieldKind, ListMeta } from './resource/listMeta'
export { VIEW_STATE_VERSION, viewStateOf, specOf, serializeView, parseView } from './resource/viewState'
export type { ViewState, Layout } from './resource/viewState'
export { businessErrorOf, errorText } from './resource/businessError'
export type { BusinessError } from './resource/businessError'
export { createResourceClient } from './resource/ResourceClient'
export type { HttpClient, ResourceClient, ImportReport, RowIssue, RowPlan } from './resource/ResourceClient'
export { useCrudResource } from './resource/useCrudResource'

// ── patterns ─────────────────────────────────────────────────────────────────
export { PermissionProvider, PermissionGate } from './patterns/PermissionGate'
export { useCan } from './patterns/permissions'
export { ActionButton } from './patterns/ActionButton'
export { ConfirmAction } from './patterns/ConfirmAction'
export { ErrorMessage, ErrorView } from './patterns/ErrorMessage'
export { QueryTable } from './patterns/QueryTable'
export type { QueryTableProps } from './patterns/QueryTable'
export { ColumnMenu } from './patterns/ColumnMenu'
export { ColumnChooser } from './patterns/ColumnChooser'
export { FilterChips } from './patterns/FilterChips'
export { SearchPanel } from './patterns/SearchPanel'
export { filterText, opLabel, panelOps, PANEL_OPS } from './patterns/listView'
export { specFromSorting } from './patterns/sorting'
export { SchemaForm } from './patterns/SchemaForm'
export type { FieldSpec, FieldType } from './patterns/SchemaForm'
export { CrudPage } from './patterns/CrudPage'
