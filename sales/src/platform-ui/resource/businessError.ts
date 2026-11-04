// One error shape from the back end: {"error": {"code", "message", "details"}}.
// The business API client decides how a failed response becomes an Error;
// this reads the shape from wherever that client put the body.

export interface BusinessError {
  code: string
  message: string
  details: Record<string, unknown>
}

function fromBody(body: unknown): BusinessError | null {
  if (!body || typeof body !== 'object') return null
  const e = (body as { error?: unknown }).error
  if (!e || typeof e !== 'object') return null
  const { code, message, details } = e as Record<string, unknown>
  if (typeof code !== 'string') return null
  return {
    code,
    message: typeof message === 'string' ? message : code,
    details: details && typeof details === 'object' ? (details as Record<string, unknown>) : {},
  }
}

/** The BusinessError carried by `err` (its `body`, `detail` or itself), or null. */
export function businessErrorOf(err: unknown): BusinessError | null {
  if (!err || typeof err !== 'object') return null
  const o = err as { body?: unknown; detail?: unknown }
  return fromBody(o.body) ?? fromBody(o.detail) ?? fromBody(err)
}

/** Text for an error: the module's message for its code, else the server's
 *  message, else the Error's own message. */
/** A server's text with its field names in the form's words (`labels`: name → label). */
export function plainFields(text: string, labels?: Record<string, string>): string {
  const names = Object.keys(labels ?? {})
  return names.length ? text.replace(new RegExp(`\\b(${names.join('|')})\\b`, 'g'), (n) => labels![n]) : text
}

export function errorText(err: unknown, messages: Record<string, string> = {}): string {
  const be = businessErrorOf(err)
  if (be) return messages[be.code] ?? be.message
  // fetch() rejects with a TypeError when the server cannot be reached at all.
  if (err instanceof TypeError) return 'The server could not be reached (it may be restarting). Try again in a moment.'
  if (err instanceof Error) return err.message
  return 'Something went wrong.'
}
