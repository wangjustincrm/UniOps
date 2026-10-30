import { useState, type ReactNode } from 'react'
import type { FieldValues } from 'react-hook-form'
import type { z } from 'zod'
import { Button } from '../primitives/shell'
import type { ColumnDef } from '../primitives/DataTable'
import { QueryTable } from './QueryTable'
import { ActionButton } from './ActionButton'
import { Sheet } from '../primitives/Sheet'
import { Dialog } from '../primitives/Dialog'
import { FilePicker } from '../primitives/FilePicker'
import { ConfirmAction } from './ConfirmAction'
import { SchemaForm, type FieldSpec } from './SchemaForm'
import { useCrudResource } from '../resource/useCrudResource'
import type { ImportReport, ResourceClient } from '../resource/ResourceClient'
import { useCan } from './permissions'
import { useListView } from './views'
import { ErrorMessage } from './ErrorMessage'

interface Row {
  id: number
  version?: number
  active?: boolean
}

export interface CrudPageProps<T extends Row, TCreate, TUpdate> {
  title: string
  /** One record's name in sheet titles ("New partner"); defaults to `title`. */
  singular?: string
  description?: string
  client: ResourceClient<T, TCreate, TUpdate>
  columns: ColumnDef<T, unknown>[]
  sortable?: string[]
  writePermission: string
  createSchema: z.ZodType<FieldValues, FieldValues>
  updateSchema: z.ZodType<FieldValues, FieldValues>
  fields: FieldSpec[]
  /** Fields that may not change after creation are shown read-only on edit. */
  immutableOnEdit?: string[]
  emptyValues: Record<string, unknown>
  toFormValues: (row: T) => Record<string, unknown>
  /** CSV export needs the business client's authenticated download. */
  onExport?: (path: string) => void
  csvImport?: boolean
  errorMessages?: Record<string, string>
  /** Shown under the form of an existing record (e.g. its audit history). */
  renderDetail?: (row: T) => ReactNode
}

/** List + create/edit form + deactivate + CSV import with a dry-run preview,
 *  for one config table served by the back end's crud_router. A user without
 *  the write permission opens a record read-only (no save, no deactivate). */
export function CrudPage<T extends Row, TCreate, TUpdate>(props: CrudPageProps<T, TCreate, TUpdate>) {
  const { client, writePermission } = props
  const view = useListView(client)
  const { spec } = view
  const { list, create, update, deactivate } = useCrudResource(client, spec, { enabled: view.ready })
  const canWrite = useCan(writePermission)
  const [editing, setEditing] = useState<T | 'new' | null>(null)
  const [confirmDeactivate, setConfirmDeactivate] = useState(false)
  const [preview, setPreview] = useState<{ file: File; report: ImportReport } | null>(null)
  const [importError, setImportError] = useState<unknown>(null)

  // The open record as the list now has it: an action in the detail panel
  // (or another user) may have changed it, and a save needs its current version.
  const editRow = editing && editing !== 'new'
    ? (list.data?.items.find((r) => r.id === editing.id) ?? editing)
    : null
  const viewOnly = editRow !== null && !canWrite
  const singular = props.singular ?? props.title
  const fields = editRow
    ? props.fields.map((f) => (props.immutableOnEdit?.includes(f.name) ? { ...f, readOnly: true } : f))
    : props.fields

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    setImportError(null)
    try {
      setPreview({ file, report: await client.importCsv(file, true) })
    } catch (e) {
      setImportError(e)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">{props.title}</h1>
        {props.description && <p className="text-sm text-neutral-500">{props.description}</p>}
      </div>
      <ErrorMessage error={importError} messages={props.errorMessages} />
      <QueryTable<T>
        columns={props.columns}
        rows={list.data?.items ?? []}
        total={list.data?.total ?? 0}
        groups={list.data?.groups}
        groupsTruncated={list.data?.groups_truncated}
        {...view.tableProps}
        resource={client}
        sortable={props.sortable}
        searchable
        loading={list.isLoading}
        error={list.error}
        errorMessages={props.errorMessages}
        onRetry={() => list.refetch()}
        rowKey={(r) => r.id}
        onRowClick={(r) => setEditing(r)}
        toolbar={
          <>
            {props.onExport && (
              <Button variant="secondary" size="sm" onClick={() => props.onExport?.(client.exportPath(spec))}>Export CSV</Button>
            )}
            {props.csvImport && canWrite && (
              <FilePicker accept=".csv,text/csv" label="CSV file" onFile={pickFile}>Import CSV</FilePicker>
            )}
            <ActionButton size="sm" permission={writePermission} onAction={() => setEditing('new')}>New</ActionButton>
          </>
        }
      />

      <Sheet
        open={editing !== null}
        onOpenChange={(o) => { if (!o) setEditing(null) }}
        title={editRow ? `${viewOnly ? '' : 'Edit '}${singular}${viewOnly ? ' (read only)' : ''}` : `New ${singular}`}
      >
        {editing !== null && (
          <SchemaForm
            key={editRow ? `${editRow.id}:${editRow.version ?? ''}` : 'new'}
            schema={editRow ? props.updateSchema : props.createSchema}
            fields={fields}
            defaultValues={editRow ? props.toFormValues(editRow) : props.emptyValues}
            changesOnly={!!editRow}
            readOnly={viewOnly}
            errorMessages={props.errorMessages}
            onCancel={() => setEditing(null)}
            onSubmit={async (values) => {
              if (editRow) await update.mutateAsync({ id: editRow.id, data: values as Partial<TUpdate>, version: editRow.version })
              else await create.mutateAsync(values as TCreate)
              setEditing(null)
            }}
          />
        )}
        {editRow && props.renderDetail && <div className="mt-6 border-t border-neutral-100 pt-4">{props.renderDetail(editRow)}</div>}
        {editRow && !viewOnly && editRow.active !== false && (
          <div className="mt-6 border-t border-neutral-100 pt-4">
            <Button variant="destructive" size="sm" onClick={() => setConfirmDeactivate(true)}>Deactivate</Button>
          </div>
        )}
      </Sheet>

      <ConfirmAction
        open={confirmDeactivate}
        onOpenChange={setConfirmDeactivate}
        title="Deactivate this record?"
        description="It stays on existing records but can no longer be chosen. It is never deleted."
        confirmLabel="Deactivate"
        destructive
        errorMessages={props.errorMessages}
        onConfirm={async () => {
          if (editRow) await deactivate.mutateAsync(editRow.id)
          setEditing(null)
        }}
      />

      <Dialog
        open={preview !== null}
        onOpenChange={(o) => { if (!o) setPreview(null) }}
        title="Import preview"
        description={preview?.file.name}
        className="max-w-2xl"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPreview(null)}>Cancel</Button>
            <ActionButton
              permission={writePermission}
              disabled={!preview?.report.ok}
              onAction={async () => {
                if (!preview) return
                try {
                  const done = await client.importCsv(preview.file, false)
                  setPreview(done.ok ? null : { ...preview, report: done })
                  if (done.ok) await list.refetch()
                } catch (e) {
                  setImportError(e)
                  setPreview(null)
                }
              }}
            >
              Import
            </ActionButton>
          </>
        }
      >
        {preview && <ImportSummary report={preview.report} />}
      </Dialog>
    </div>
  )
}

function ImportSummary({ report }: { report: ImportReport }) {
  if (!report.ok) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-danger-600">Nothing will be written. Fix these {report.errors.length} problem(s) and import again:</p>
        <ul className="max-h-72 overflow-y-auto text-sm">
          {report.errors.map((e, i) => (
            <li key={i} className="border-t border-neutral-100 py-1">
              Row {e.row}{e.column ? ` · ${e.column}` : ''}: {e.message}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  const changed = report.rows.filter((r) => r.action !== 'unchanged')
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>{report.counts.create} new · {report.counts.update} changed · {report.counts.unchanged} unchanged</p>
      <ul className="max-h-72 overflow-y-auto">
        {changed.map((r) => (
          <li key={r.row} className="border-t border-neutral-100 py-1">
            <span className="font-medium">{r.key}</span> — {r.action}
            {r.action === 'update' && r.after && (
              <span className="text-neutral-500">
                {' '}({Object.keys(r.after).map((k) => `${k}: ${String(r.before?.[k] ?? '—')} → ${String(r.after?.[k] ?? '—')}`).join(', ')})
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
