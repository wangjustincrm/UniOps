import type { FieldSpec } from './SchemaForm'

export const sizeText = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.ceil(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

/** Why this file cannot go (type or size), or null. */
export function fileProblem(f: FieldSpec, file: unknown): string | null {
  if (!(file instanceof File)) return null
  const types = (f.accept ?? '').split(',').map((t) => t.trim().toLowerCase()).filter((t) => t.startsWith('.'))
  if (types.length && !types.some((t) => file.name.toLowerCase().endsWith(t))) return `Choose a ${types.join(', ')} file.`
  if (f.maxBytes && file.size > f.maxBytes) return `Too large: at most ${sizeText(f.maxBytes)}.`
  if (file.size === 0) return 'The file is empty.'
  return null
}

