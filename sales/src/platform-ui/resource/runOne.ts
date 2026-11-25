import type { ActionOut, ActionResultRow, ActionRunBody } from './ResourceClient'

/** One record's action that was left out, or failed: an error, with the server's code and words (read as any business
 *  error: `businessErrorOf`, `errorText`, `isStale`). Never a success. */
export class ActionRefused extends Error {
  readonly body: { error: { code: string; message: string; details: Record<string, unknown> } }
  constructor(row: ActionResultRow | undefined) {
    const message = row?.reason ?? 'It was not done.'
    super(message)
    this.name = 'ActionRefused'
    this.body = { error: { code: row?.code ?? `action.${row?.status ?? 'unknown'}`, message, details: {} } }
  }
}

/** What the status row says of one record's action that was done: its problem as an error, else its note, else `text`. */
export function actionDone(row: ActionResultRow, text: string, key?: string): { tone: 'error' | 'success'; text: string; key?: string } {
  const problem = problemDone(row.problem)
  return problem
    ? { ...problem, ...(key ? { key } : {}) }
    : { tone: 'success', text: row.note ?? text, ...(key ? { key } : {}) }
}

/** For a form's answer that can say what went wrong though it was saved: as an error, or nothing. */
export const problemDone = (problem: string | null | undefined): { tone: 'error'; text: string } | null =>
  (problem ? { tone: 'error', text: `${problem[0].toUpperCase()}${problem.slice(1)}` } : null)

/** A record action on ONE record, by the version the person saw (null: a record that has none): its result when it was
 *  done; left out or failed is thrown. A button and a confirmation use it as any other call. */
export async function runOne(
  client: { action: (actionId: string, body: ActionRunBody) => Promise<ActionOut> },
  actionId: string,
  record: { id: number; version: number | null },
  given: { input?: unknown; reason?: string } = {},
): Promise<ActionResultRow> {
  const out = await client.action(actionId, { ids: { [String(record.id)]: record.version }, ...given })
  const row = out.results.find((r) => r.id === record.id)
  if (row?.status !== 'done') throw new ActionRefused(row)
  return row
}
