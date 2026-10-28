// UI library — see README.md. Business code imports from
// '@/platform-ui' only; nothing in this folder imports business code.

export { cn } from './lib/cn'
export { toSearchParams, defaultSpec, MAX_PAGE_SIZE } from './lib/querySpec'
export type { QuerySpec, SortKey, Filter, FilterOp, FilterValue, Page } from './lib/querySpec'
export { businessErrorOf, errorText } from './lib/businessError'
export type { BusinessError } from './lib/businessError'

export { createResourceClient } from './resource/ResourceClient'
export type { HttpClient, ResourceClient, ImportReport, RowIssue, RowPlan } from './resource/ResourceClient'
export { useCrudResource } from './resource/useCrudResource'

export { PermissionProvider, PermissionGate } from './permissions/PermissionGate'
export { useCan } from './permissions/context'

export { Dialog } from './overlays/Dialog'
export { Sheet } from './overlays/Sheet'
export { Popover } from './overlays/Popover'
export { ConfirmDialog } from './overlays/Confirm'
export { ToastProvider } from './overlays/Toast'
export { useToast } from './overlays/toastContext'
export type { ToastInput, ToastVariant } from './overlays/toastContext'
export { Tabs } from './nav/Tabs'
export type { TabItem } from './nav/Tabs'
export { Combobox } from './inputs/Combobox'
export type { ComboOption } from './inputs/Combobox'

export { DataTable } from './data/DataTable'
export { specFromSorting } from './data/sorting'
export type { ColumnDef, DataTableProps } from './data/DataTable'
export { StatusBadge } from './data/StatusBadge'
export type { StatusStyle, BadgeVariant } from './data/StatusBadge'
export { ActionButton } from './data/ActionButton'

export { EmptyState, ErrorState, ErrorMessage } from './states/States'
export { SchemaForm } from './forms/SchemaForm'
export type { FieldSpec, FieldType } from './forms/SchemaForm'
export { CrudPage } from './crud/CrudPage'
