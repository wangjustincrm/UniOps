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
export type { ComboOption } from './primitives/Combobox'
export { DataTable } from './primitives/DataTable'
export type { ColumnDef, DataTableProps, SortValue } from './primitives/DataTable'
export { StatusBadge } from './primitives/StatusBadge'
export type { StatusStyle, BadgeVariant } from './primitives/StatusBadge'
export { EmptyState, ErrorState, ErrorLine } from './primitives/States'

// ── resource ─────────────────────────────────────────────────────────────────
export {
  toSearchParams, defaultSpec, MAX_PAGE_SIZE, clampPageSize, getFilter, setFilter, clearFilter,
} from './resource/querySpec'
export type { QuerySpec, SortKey, Filter, FilterOp, FilterValue, Page } from './resource/querySpec'
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
export type { QueryTableProps, FilterControl } from './patterns/QueryTable'
export { specFromSorting } from './patterns/sorting'
export { SchemaForm } from './patterns/SchemaForm'
export type { FieldSpec, FieldType } from './patterns/SchemaForm'
export { CrudPage } from './patterns/CrudPage'
