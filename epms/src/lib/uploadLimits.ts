// What each upload area accepts, stated and checked in one place (ISS-0093).
//
// Invoice files: PRD INV-001 — PDF, JPG, JPEG, PNG; at most 25 MB. epms-api
// refuses any other file name on POST /invoices, and expense-api refuses files
// over 25 MB.
//
// Document attachments (PR, PO, GR, PA): the File Server's allow-list (PRD
// §3.4.1, file-api ALLOWED_CONTENT_TYPES) — every other type is refused there
// anyway, after the document was saved. Size: 25 MB, which is what epms-api's
// attachment endpoints enforce (*_attachments.py MAX_FILE_SIZE); the File
// Server itself would take 50 MB.

const MB = 1024 * 1024

export interface UploadRule {
  /** For <input accept>. */
  accept: string
  extensions: string[]
  maxBytes: number
  /** The line shown in the drop zone. */
  hint: string
  /** The types, as named in a refusal. */
  typesLabel: string
}

function rule(extensions: string[], maxMb: number, typesLabel: string): UploadRule {
  return {
    accept: extensions.map((e) => `.${e}`).join(','),
    extensions,
    maxBytes: maxMb * MB,
    hint: `${typesLabel} · Max ${maxMb} MB per file`,
    typesLabel,
  }
}

export const INVOICE_FILE_RULE = rule(['pdf', 'jpg', 'jpeg', 'png'], 25, 'PDF, JPG, JPEG, PNG')

export const ATTACHMENT_RULE = rule(
  ['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'xlsx', 'xls', 'docx', 'doc', 'csv', 'txt', 'msg', 'eml'],
  25,
  'PDF, images, Excel, Word, CSV, text, emails (.msg / .eml)',
)

function extensionOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i + 1).toLowerCase()
}

/** Why this file cannot be uploaded under `rule`, or null when it can. */
export function uploadProblem(file: { name: string; size: number }, r: UploadRule): string | null {
  if (!r.extensions.includes(extensionOf(file.name))) {
    return `"${file.name}" cannot be uploaded: only ${r.typesLabel} files are accepted.`
  }
  if (file.size > r.maxBytes) {
    return `"${file.name}" is larger than ${r.maxBytes / MB} MB.`
  }
  return null
}

/** Splits a selection into the files that may be uploaded and the refusals. */
export function partitionUploads<F extends { name: string; size: number }>(
  files: F[], r: UploadRule,
): { ok: F[]; problems: string[] } {
  const ok: F[] = []
  const problems: string[] = []
  for (const f of files) {
    const p = uploadProblem(f, r)
    if (p) problems.push(p)
    else ok.push(f)
  }
  return { ok, problems }
}
