import { useEffect, useState } from 'react'
import { z } from 'zod'
import type { FieldValues } from 'react-hook-form'
import type { ActionOut, ActionRunBody } from '../resource/ResourceClient'
import { errorText } from '../resource/businessError'
import { Stack } from '../primitives/Layout'
import { Hint, Text } from '../primitives/Text'
import { FormDialog } from './FormDialog'
import { SchemaForm, type FieldSpec } from './SchemaForm'
import { resultText, type ActionHost, type RecordAction } from './recordActions'

/** What an action runs on: these records (as the person saw them), or every record the list's query matches now. */
export type ActionTarget<T> = { rows: T[] } | { query: string; expected: number }

const SHOWN = 5  // skipped / failed records listed by name in the preview; the rest counted

/** The confirm step of a record action: the server's dry run ("10 will be marked settled, 2 skipped: why"), the
 *  action's form (or a reason), then the run; the result goes to the status row of the list or sheet it is in. */
export function RecordActionDialog<T extends { id: number; version: number }>({ action, target, host, open, onOpenChange, onDone }: {
  action: RecordAction<T>
  target: ActionTarget<T>
  host: ActionHost<T>
  open: boolean
  onOpenChange: (open: boolean) => void
  /** After the run (the selection clears, the list reads again). */
  onDone?: () => void
}) {
  const [preview, setPreview] = useState<ActionOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const rows = 'rows' in target ? target.rows : []
  const n = 'rows' in target ? target.rows.length : target.expected
  const names = new Map(rows.map((r) => [r.id, host.name(r)]))
  const nameOf = (id: number) => names.get(id) ?? `#${id}`
  const body = (): ActionRunBody => ('rows' in target
    ? { ids: Object.fromEntries(target.rows.map((r) => [String(r.id), r.version])) }
    : { query: target.query, expected: target.expected })
  useEffect(() => {
    if (!open) return  // mounted per run (its callers render it while it is open): no state to reset
    let live = true
    host.run(action.id, { ...body(), dry_run: true }).then((p) => { if (live) setPreview(p) },
      (e: unknown) => { if (live) setError(errorText(e)) })
    return () => { live = false }
  }, [open])  // eslint-disable-line react-hooks/exhaustive-deps
  const input = action.input
  const form: { schema: z.ZodType<FieldValues, FieldValues>; fields: FieldSpec[]; defaults: Record<string, unknown> } = !input
    ? { schema: z.object({}), fields: [] as FieldSpec[], defaults: {} }
    : 'reason' in input
      ? { schema: z.object({ reason: z.string().trim().min(3, 'At least 3 characters') }),
          fields: [{ name: 'reason', label: input.reason.label, type: 'textarea', required: true }] as FieldSpec[], defaults: { reason: '' } }
      : { schema: input.schema, fields: input.fields, defaults: input.defaults() }
  const toInput = (v: Record<string, unknown>) => (input && 'schema' in input && input.toBody ? input.toBody(v) : v)
  const left = preview ? preview.results.filter((r) => r.status !== 'done') : []
  const will = preview?.counts.done ?? 0
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={action.title?.(n) ?? `${action.label}: ${n} ${n === 1 ? 'record' : 'records'}`}
      description={action.description}>
      <Stack gap={3}>
        <div data-action-preview>
          {!preview && !error && <Hint>Checking which of them it applies to…</Hint>}
          {error && <Text tone="danger">Could not check them: {error}</Text>}
          {preview && (
            <Stack gap={1}>
              <Text weight="medium">{will ? action.will(will) : 'It applies to none of them.'}</Text>
              {left.length > 0 && <Text tone="secondary">{left.length} {left.length === 1 ? 'is' : 'are'} left out:</Text>}
              {left.length > 0 && (
                <Stack as="ul" gap={0.5}>
                  {left.slice(0, SHOWN).map((r) => (
                    <Text as="li" key={r.id} size="xs" tone="secondary">{nameOf(r.id)}: {r.reason}</Text>
                  ))}
                  {left.length > SHOWN && <Text as="li" size="xs" tone="muted">and {left.length - SHOWN} more</Text>}
                </Stack>
              )}
            </Stack>
          )}
        </div>
        {preview && will > 0 && (
          <SchemaForm schema={form.schema} fields={form.fields} defaultValues={form.defaults}
            submitLabel={`${action.label} (${will})`} onCancel={() => onOpenChange(false)}
            onSubmit={(v) => host.run(action.id, { ...body(), input: toInput(v) })}
            done={(res) => {
              const out = res as ActionOut
              return { key: `action:${action.id}`, tone: out.counts.failed ? 'warning' : 'success', text: resultText(action, out, nameOf) }
            }}
            after={() => { onOpenChange(false); onDone?.() }} />
        )}
      </Stack>
    </FormDialog>
  )
}
