import type { FieldErrors, FieldValues, Resolver } from 'react-hook-form'
import type { z } from 'zod'

// A small zod → react-hook-form resolver. @hookform/resolvers is not used: its
// optional peer ajv@8 leaves a lock file that npm 10's `npm ci` rejects as out
// of sync (checked 2026-10-27), and config forms need only this much.
//
// Errors come back in react-hook-form's shape: nested objects for nested
// paths (`{address: {city: {...}}}`, not "address.city"), and issues with an
// empty path — an object-level refine — under `root`, which blocks the submit
// like any field error and is shown by SchemaForm.
type Leaf = { type: string; message: string }
type Node = { [key: string]: Node | Leaf }

const without = (values: FieldValues, names: string[]) =>
  Object.fromEntries(Object.entries(values).filter(([k]) => !names.includes(k)))

// All the issues were on fields that do not apply, so zod gave no parsed
// values: each remaining field is parsed by itself (its trim, its transform).
async function parsedFields(schema: z.ZodType<FieldValues, FieldValues>, values: FieldValues): Promise<FieldValues> {
  const shape = (schema as unknown as { shape?: Record<string, z.ZodType> }).shape ?? {}
  const out = { ...values }
  for (const [k, field] of Object.entries(shape)) {
    if (!(k in out)) continue
    const r = await field.safeParseAsync(out[k])
    if (r.success) out[k] = r.data
  }
  return out
}

/** `hidden` names the fields that do not apply to these values: they are not
 *  validated (an issue on one is dropped) and not in the result. */
export function zodResolver(schema: z.ZodType<FieldValues, FieldValues>, hidden: (values: FieldValues) => string[] = () => []): Resolver<FieldValues> {
  return async (values) => {
    const skip = hidden(values)
    const r = await schema.safeParseAsync(values)
    if (r.success) return { values: without(r.data, skip), errors: {} }
    const issues = r.error.issues.filter((i) => !skip.includes(String(i.path[0])))
    if (issues.length === 0) return { values: await parsedFields(schema, without(values, skip)), errors: {} }
    const errors: Node = {}
    for (const issue of issues) {
      const leaf: Leaf = { type: issue.code, message: issue.message }
      const path = issue.path.map(String)
      if (path.length === 0) {
        if (!('root' in errors)) errors.root = leaf
        continue
      }
      let node = errors
      for (const seg of path.slice(0, -1)) {
        const next = node[seg]
        if (!next || 'message' in next) node[seg] = { ...(next as Node | undefined) }
        node = node[seg] as Node
      }
      const last = path[path.length - 1]
      if (!(last in node)) node[last] = leaf
    }
    return { values: {}, errors: errors as unknown as FieldErrors<FieldValues> }
  }
}
