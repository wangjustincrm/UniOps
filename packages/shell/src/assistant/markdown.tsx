/**
 * A small Markdown renderer for Assistant replies (ISS-0012).
 *
 * The model answers in Markdown — bold, lists, tables — and the panel printed
 * it raw: "**Issued**", "| 协议号 | 标题 |", "|------|". This renders the subset
 * the replies actually use, and nothing else.
 *
 * Deliberately not a library and not HTML: every piece becomes a React element
 * whose text React escapes, so a reply (which can quote vendor names, titles,
 * comments other people typed) can never inject markup. No links, no images,
 * no raw HTML — anything outside the subset is shown as the text it is.
 *
 * Supported: paragraphs and line breaks, # / ## / ### headings, "-" / "*" /
 * "1." lists, pipe tables (header + --- separator row), **bold**, *italic*,
 * `code`.
 */
import type { ReactNode } from 'react'

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/
const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/
const HEADING = /^\s*(#{1,4})\s+(.*)$/

function cells(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map((c) => c.trim())
}

/** **bold**, *italic*, `code` — in that order of precedence. */
export function renderInline(text: string, keyBase = 'i'): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(\*[^*\s][^*\n]*?\*)/g
  let last = 0
  let m: RegExpExecArray | null
  let n = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyBase}-${n++}`
    if (m[1]) out.push(<code key={key} className="rounded bg-neutral-200/70 px-1 font-mono text-[0.85em]">{tok.slice(1, -1)}</code>)
    else if (m[2]) out.push(<strong key={key} className="font-semibold">{tok.slice(2, -2)}</strong>)
    else out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

type Block =
  | { kind: 'p'; lines: string[] }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; start: number; items: string[] }
  | { kind: 'table'; head: string[]; rows: string[][] }

export function parseBlocks(text: string): Block[] {
  const lines = (text ?? '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }

    // A table needs a header row AND a separator row right under it; a lone
    // line containing "|" is ordinary text.
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const head = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(cells(lines[i]))
        i++
      }
      blocks.push({ kind: 'table', head, rows })
      continue
    }
    const h = HEADING.exec(line)
    if (h) { blocks.push({ kind: 'h', level: h[1].length, text: h[2] }); i++; continue }
    if (BULLET.test(line)) {
      const items: string[] = []
      while (i < lines.length && BULLET.test(lines[i])) { items.push(BULLET.exec(lines[i])![1]); i++ }
      blocks.push({ kind: 'ul', items })
      continue
    }
    if (NUMBERED.test(line)) {
      const start = Number(NUMBERED.exec(line)![1])
      const items: string[] = []
      while (i < lines.length && NUMBERED.test(lines[i])) { items.push(NUMBERED.exec(lines[i])![2]); i++ }
      blocks.push({ kind: 'ol', start, items })
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !HEADING.test(lines[i]) && !BULLET.test(lines[i])
           && !NUMBERED.test(lines[i])
           && !(lines[i].includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))) {
      para.push(lines[i]); i++
    }
    blocks.push({ kind: 'p', lines: para })
  }
  return blocks
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text)
  return (
    <div className="space-y-2 break-words">
      {blocks.map((b, bi) => {
        const k = `b${bi}`
        switch (b.kind) {
          case 'h':
            return <p key={k} className="font-semibold text-neutral-900">{renderInline(b.text, k)}</p>
          case 'ul':
            return (
              <ul key={k} className="list-disc space-y-0.5 pl-5">
                {b.items.map((it, ii) => <li key={ii}>{renderInline(it, `${k}-${ii}`)}</li>)}
              </ul>
            )
          case 'ol':
            return (
              <ol key={k} start={b.start} className="list-decimal space-y-0.5 pl-5">
                {b.items.map((it, ii) => <li key={ii}>{renderInline(it, `${k}-${ii}`)}</li>)}
              </ol>
            )
          case 'table':
            return (
              <div key={k} className="overflow-x-auto">
                <table className="min-w-full border-collapse text-xs">
                  <thead>
                    <tr>{b.head.map((c, ci) => (
                      <th key={ci} className="border-b border-neutral-300 px-2 py-1 text-left font-semibold">
                        {renderInline(c, `${k}-h${ci}`)}
                      </th>))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, ri) => (
                      <tr key={ri} className="border-b border-neutral-200 last:border-0">
                        {b.head.map((_, ci) => (
                          <td key={ci} className="px-2 py-1 align-top">{renderInline(r[ci] ?? '', `${k}-${ri}-${ci}`)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          default:
            return (
              <p key={k}>
                {b.lines.map((l, li) => (
                  <span key={li}>{li > 0 && <br />}{renderInline(l, `${k}-${li}`)}</span>
                ))}
              </p>
            )
        }
      })}
    </div>
  )
}
