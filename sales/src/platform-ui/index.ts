// The module's UI library (design §13.15). Three layers, one way:
//   primitives/  visual only — imports primitives, lib and @uniops/shell
//   resource/    talking to the API — ResourceClient, querySpec, businessError
//   patterns/    composes primitives and resource — permissions, forms, tables
// Pages and features import from '@/platform-ui' only; this barrel is what they
// use, nothing more. A component a feature needs for the first time is added
// here; tests reach what they need by its direct path.


// ── primitives ───────────────────────────────────────────────────────────────
export { Badge, Button, FormField, Skeleton } from './primitives/shell'
export { Dialog } from './primitives/Dialog'
export { Timeline } from './primitives/Timeline'
export type { TimelineItem } from './primitives/Timeline'
export { CopyChip } from './primitives/Copy'
export { TrackingLink } from './primitives/TrackingLink'
export { Hint, Muted } from './primitives/Text'
export { ToastProvider } from './primitives/Toast'
export { Tabs } from './primitives/Tabs'
export { PageHeader } from './primitives/PageHeader'
export { DetailList } from './primitives/DetailList'
export {
  BooleanCell, CountdownCell, DateTimeCell, DateCell, MoneyCell, NumberCell, PlaceCell, RefsCell, RelativeCell, StackedDateTimeCell, StatusCell, TagsCell,
  type RecordRef,
} from './primitives/cells'
export { columnsOf } from './primitives/columns'
export {
  dateAndTime, formatDateTime, formatDate, formatDuration, formatTime, formatMoney, formatNumber, registerStatusTones, statusTone, timeFromNow,
} from './primitives/format'
export { useNow } from './primitives/useNow'
export { Autocomplete } from './primitives/Autocomplete'
export { MultiChoice } from './primitives/MultiChoice'
export { MultiSelect } from './primitives/MultiSelect'
export type { MultiOption } from './primitives/MultiSelect'
export { submitOnEnter } from './primitives/submitOnEnter'
export { DateInput, DateTimeInput, Textarea } from './primitives/FieldInputs'
export { todayDate } from './primitives/dateValues'
export type { ComboOption } from './primitives/Combobox'
export type { ColumnDef } from './primitives/DataTable'
export { LocalTable } from './primitives/LocalTable'
export { numberColumn, textColumn, readColumn, selectColumn } from './primitives/editableColumns'
export type { EditableColumn } from './primitives/EditableTable'
export { StatCard } from './primitives/StatCard'
export { IconButton } from './primitives/IconButton'
export { Tooltip } from './primitives/Tooltip'
export { EmptyState } from './primitives/States'

// ── resource ─────────────────────────────────────────────────────────────────
export type { QuerySpec, Filter } from './resource/querySpec'
export { useListMeta } from './resource/listMeta'
export { createViewsClient } from './resource/viewsApi'
export { requestOpen, requestView } from './resource/viewRequests'
export { businessErrorOf } from './resource/businessError'
export { createResourceClient } from './resource/ResourceClient'
export { queryKeys } from './resource/queryKeys'

// ── patterns ─────────────────────────────────────────────────────────────────
export { PermissionProvider } from './patterns/PermissionGate'
export { useCan } from './patterns/permissions'
export { ActionButton } from './patterns/ActionButton'
export { ConfirmAction } from './patterns/ConfirmAction'
export { RowActions } from './patterns/RowActions'
export { MasterDetailForm } from './patterns/MasterDetailForm'
export { ADDRESS_FIELDS, addressSchema, cleanAddress } from './patterns/address'
export { AddressView } from './primitives/AddressView'
export { DiffTable } from './patterns/DiffTable'
export { ErrorMessage, ErrorView } from './patterns/ErrorMessage'
export { ListPage } from './patterns/ListPage'
export type { ListCreate } from './patterns/ListPage'
export { ViewsProvider } from './patterns/ViewsProvider'
export { SchemaForm } from './patterns/SchemaForm'
export type { FieldSpec } from './patterns/SchemaForm'
export { CrudPage } from './patterns/CrudPage'
export { useRefresh, compareRecord } from './patterns/useRefresh'
export { useNotices } from './patterns/notices'
export type { NoticeInput } from './patterns/notices'
export { NoticeScope } from './patterns/TableStatusBar'
export { Callout } from './primitives/Callout'
export { RecordSheet, RecordSheetHostContext, FormSheet } from './patterns/RecordSheet'
export { Section } from './patterns/Section'
export type { RecordFact, RecordTab } from './patterns/RecordSheet'
