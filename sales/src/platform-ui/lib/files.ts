/** A file as the browser holds it (2026-11-12): read with the token, then shown (FileViewer) or saved. */
export interface FileData { blob: Blob; name: string; type: string }

/** Saves a file as the browser's download. */
export function saveFile(f: FileData): void {
  const href = URL.createObjectURL(f.blob)
  const a = document.createElement('a')
  a.href = href
  a.download = f.name
  a.click()
  URL.revokeObjectURL(href)
}

export type FileKind = 'pdf' | 'image' | 'text' | 'other'

const TEXT_EXT = /\.(txt|csv|tsv|json|xml|edi|x12|dat|log|html?|[0-9]{3})$/i
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg)$/i

/** What a file is, from its type, its name, and (when those say nothing) its first bytes:
 *  "%PDF" is a PDF; bytes with no NUL that decode as UTF-8 are text (an EDI file, an 810 JSON);
 *  anything else (a zip) is not shown, only saved. */
export function fileKind(f: FileData, head: Uint8Array): FileKind {
  const t = f.type.toLowerCase()
  if (t === 'application/pdf' || /\.pdf$/i.test(f.name) || startsWith(head, '%PDF')) return 'pdf'
  if (t.startsWith('image/') || IMAGE_EXT.test(f.name)) return 'image'
  if (t === 'application/zip' || /\.zip$/i.test(f.name) || startsWith(head, 'PK')) return 'other'
  if (t.startsWith('text/') || t.includes('json') || t.includes('xml') || TEXT_EXT.test(f.name)) return 'text'
  return looksLikeText(head) ? 'text' : 'other'
}

function startsWith(head: Uint8Array, s: string): boolean {
  return s.split('').every((c, i) => head[i] === c.charCodeAt(0))
}

function looksLikeText(head: Uint8Array): boolean {
  if (head.includes(0)) return false
  try {
    new TextDecoder('utf-8', { fatal: true }).decode(head.subarray(0, Math.max(0, head.length - 4)))  // a cut character at the end is fine
    return true
  } catch {
    return false
  }
}

/** Text made readable, or null when it is already as readable as it gets: JSON indented; an X12
 *  file (ISA…) one segment per line — its segment terminator is the character after ISA16. */
export function formatText(raw: string): string | null {
  const s = raw.replace(/^\uFEFF/, '')
  const trimmed = s.trimStart()
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const pretty = JSON.stringify(JSON.parse(trimmed), null, 2)
      return pretty === s ? null : pretty
    } catch {
      return null
    }
  }
  if (trimmed.startsWith('ISA') && trimmed.length > 106) {
    const term = trimmed[105]
    if (!term || /[A-Za-z0-9 ]/.test(term)) return null
    const out = trimmed.split(term).map((x) => x.replace(/^[\r\n]+|[\r\n]+$/g, '')).filter(Boolean).join(`${term}\n`) + term
    return out === s ? null : out
  }
  return null
}
