import type { FieldErrors, FieldValues, Resolver } from 'react-hook-form'
import type { z } from 'zod'

// A small zod → react-hook-form resolver. @hookform/resolvers is not used: its
// optional peer ajv@8 leaves a lock file that npm 10's `npm ci` rejects as out
// of sync (checked 2026-10-27), and config forms need only this much.
export function zodResolver(schema: z.ZodType<FieldValues, FieldValues>): Resolver<FieldValues> {
  return async (values) => {
    const r = await schema.safeParseAsync(values)
    if (r.success) return { values: r.data, errors: {} }
    const errors: Record<string, { type: string; message: string }> = {}
    for (const issue of r.error.issues) {
      const path = issue.path.map(String).join('.')
      if (path && !(path in errors)) errors[path] = { type: issue.code, message: issue.message }
    }
    return { values: {}, errors: errors as FieldErrors<FieldValues> }
  }
}
