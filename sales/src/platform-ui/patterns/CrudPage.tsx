import { useState, type ReactNode } from 'react'
import type { FieldValues } from 'react-hook-form'
import type { z } from 'zod'
import { Button } from '../primitives/shell'
import type { ColumnDef } from '../primitives/DataTable'
import { Dialog } from '../primitives/Dialog'
import { FilePicker } from '../primitives/FilePicker'
import { ActionButton } from './ActionButton'
import { ConfirmAction } from './ConfirmAction'
import { ListPage } from './ListPage'
import { SchemaForm, type FieldSpec } from './SchemaForm'
import { useCrudResource } from '../resource/useCrudResource'
import type { ImportReport, ResourceClient } from '../resource/ResourceClient'
import { useCan } from './permissions'
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
  /** Shown between the header and the list (e.g. a related setting). */
  notice?: ReactNode
}

/** List + create/edit form + deactivate + CSV import with a dry-run preview,
 *  for one config table served by the back end's crud_router — a ListPage
 *  whose detail is the record's form. A user without the write permission
 *  opens a record read-only (no save, no deactivate). */
export function CrudPage<T extends Row, TCreate, TUpdate>(props: CrudPageProps<T, TCreate, TUpdate>) {
  const { client, writePermission } = props
  const { create, update, deactivate, refresh } = useCrudResource(client)
  const canWrite = useCan(writePermission)
  const [preview, setPreview] = useState<{ file: File; report: ImportReport } | null>(null)
  const [importError, setImportError] = useState<unknown>(null)
  const singular = props.singular ?? props.title

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
    <ListPage<T>
      resource={client}
      title={props.title}
      description={props.description}
      columns={props.columns}
      errorMessages={props.errorMessages}
      notice={<>{props.notice}<ErrorMessage error={importError} messages={props.errorMessages} /></>}
      detailTitle={() => (canWrite ? `Edit ${singular}` : `${singular} (read only)`)}
      renderDetail={(row, close) => (
        <RecordForm row={row} close={close} canWrite={canWrite} props={props}
          save={(values) => update.mutateAsync({ id: row.id, data: values as Partial<TUpdate>, version: row.version })}
          deactivate={() => deactivate.mutateAsync(row.id)} />
      )}
      toolbar={({ spec }) => (
        <>
          {props.onExport && (
            <Button variant="secondary" size="sm" onClick={() => props.onExport?.(client.exportPath(spec))}>Export CSV</Button>
          )}
          {props.csvImport && canWrite && (
            <FilePicker accept=".csv,text/csv" label="CSV file" onFile={pickFile}>Import CSV</FilePicker>
          )}
        </>
      )}
      create={{
        label: 'New', permission: writePermission, title: `New ${singular}`,
        render: (done, cancel) => (
          <SchemaForm
            key="new"
            schema={props.createSchema}
            fields={props.fields}
            defaultValues={props.emptyValues}
            errorMessages={props.errorMessages}
            onCancel={cancel}
            onSubmit={async (values) => done(await create.mutateAsync(values as TCreate))}
          />
        ),
      }}
    >
      {() => (
        <>
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
                      if (done.ok) await refresh()
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
        </>
      )}
    </ListPage>
  )
}

/** An existing record: its form (read-only without the write permission),
 *  the page's extra detail, and Deactivate. */
function RecordForm<T extends Row, TCreate, TUpdate>({ row, close, canWrite, props, save, deactivate }: {
  row: T
  close: () => void
  canWrite: boolean
  props: CrudPageProps<T, TCreate, TUpdate>
  save: (values: Record<string, unknown>) => Promise<unknown>
  deactivate: () => Promise<unknown>
}) {
  const [confirm, setConfirm] = useState(false)
  const fields = props.fields.map((f) => (props.immutableOnEdit?.includes(f.name) ? { ...f, readOnly: true } : f))
  return (
    <>
      <SchemaForm
        key={`${row.id}:${row.version ?? ''}`}
        schema={props.updateSchema}
        fields={fields}
        defaultValues={props.toFormValues(row)}
        changesOnly
        readOnly={!canWrite}
        errorMessages={props.errorMessages}
        onCancel={close}
        onSubmit={async (values) => {
          await save(values)
          close()
        }}
      />
      {props.renderDetail && <div className="mt-6 border-t border-neutral-100 pt-4">{props.renderDetail(row)}</div>}
      {canWrite && row.active !== false && (
        <div className="mt-6 border-t border-neutral-100 pt-4">
          <Button variant="destructive" size="sm" onClick={() => setConfirm(true)}>Deactivate</Button>
        </div>
      )}
      <ConfirmAction
        open={confirm}
        onOpenChange={setConfirm}
        title="Deactivate this record?"
        description="It stays on existing records but can no longer be chosen. It is never deleted."
        confirmLabel="Deactivate"
        destructive
        errorMessages={props.errorMessages}
        onConfirm={async () => {
          await deactivate()
          close()
        }}
      />
    </>
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
