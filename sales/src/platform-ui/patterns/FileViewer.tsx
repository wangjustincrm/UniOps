import { Download, Eye, ExternalLink, Printer } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { fileKind, formatText, saveFile, type FileData, type FileKind } from '../lib/files'
import { Dialog } from '../primitives/Dialog'
import { ErrorLine } from '../primitives/States'
import { Button, type ButtonProps } from '../primitives/shell'

/** A file to look at (2026-11-12): what to call it, and how to read it (with the token). `key`
 *  tells two files apart (a new key reads again). */
export interface FileSource { key: string; title: string; load: () => Promise<FileData> }

/** Text past this is shown cut, with how much more there is; Download has it all. */
const TEXT_LIMIT = 2_000_000

type Ready = { file: FileData; kind: FileKind; url: string; raw?: string; pretty?: string | null; cut?: number }
type State = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; view: Ready }

/** The bytes of a blob (FileReader: every browser, and the test DOM, which has no Blob.arrayBuffer). */
function readBytes(b: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer))
    r.onerror = () => reject(r.error ?? new Error('the file could not be read'))
    r.readAsArrayBuffer(b)
  })
}

async function prepare(file: FileData): Promise<Omit<Ready, 'url'>> {
  const head = await readBytes(file.blob.slice(0, 4096))
  const kind = fileKind(file, head)
  if (kind !== 'text') return { file, kind }
  const all = new TextDecoder('utf-8').decode(await readBytes(file.blob))
  const raw = all.length > TEXT_LIMIT ? all.slice(0, TEXT_LIMIT) : all
  return { file, kind, raw, pretty: formatText(raw), cut: all.length - raw.length }
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** Prints through a hidden frame of its own: the page around it is never printed. */
function printFrame(html: string, title: string) {
  const f = document.createElement('iframe')
  f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0'
  f.srcdoc = `<!doctype html><title>${esc(title)}</title><body style="margin:12mm">${html}</body>`
  f.onload = () => { f.contentWindow?.focus(); f.contentWindow?.print(); setTimeout(() => f.remove(), 1000) }
  document.body.appendChild(f)
}

function Body({ source, onReady }: { source: FileSource; onReady: (v: Ready | null) => void }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [asReceived, setAsReceived] = useState(false)
  useEffect(() => {
    let live = true
    let url: string | null = null
    source.load().then(prepare).then((v) => {
      if (!live) return
      url = URL.createObjectURL(v.kind === 'pdf' ? new Blob([v.file.blob], { type: 'application/pdf' }) : v.file.blob)
      const view = { ...v, url }
      setState({ status: 'ready', view })
      onReady(view)
    }).catch((e: unknown) => {
      if (live) setState({ status: 'error', message: e instanceof Error ? e.message : String(e) })
    })
    return () => { live = false; if (url) URL.revokeObjectURL(url); onReady(null) }
  }, [source, onReady])

  if (state.status === 'loading') return <p className="py-16 text-center text-sm text-neutral-500">Opening {source.title}…</p>
  if (state.status === 'error') return <ErrorLine message={`Could not open the file: ${state.message}`} />
  const v = state.view
  if (v.kind === 'pdf') return <iframe title={source.title} src={v.url} className="h-[75vh] w-full rounded border border-neutral-200" />
  if (v.kind === 'image') return <img alt={source.title} src={v.url} className="mx-auto max-h-[75vh] max-w-full" />
  if (v.kind === 'other') {
    return <p className="py-12 text-center text-sm text-neutral-500">{v.file.name} cannot be shown here: download it to open it.</p>
  }
  const shown = v.pretty && !asReceived ? v.pretty : v.raw ?? ''
  return (
    <div className="space-y-2">
      {v.pretty && (
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <span>{asReceived ? 'As the file is.' : 'Laid out to read (one segment or field per line); the file itself is unchanged.'}</span>
          <Button size="sm" variant="secondary" onClick={() => setAsReceived(!asReceived)}>{asReceived ? 'Laid out' : 'As the file is'}</Button>
        </div>
      )}
      <pre aria-label={`${source.title} contents`} className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-all rounded border border-neutral-200 bg-neutral-50 p-3 font-mono text-xs leading-5 text-neutral-800">{shown}</pre>
      {(v.cut ?? 0) > 0 && <p className="text-xs text-neutral-500">Only the start is shown ({(v.cut ?? 0).toLocaleString()} more characters): download it for all.</p>}
    </div>
  )
}

/** Looks at a file before (or instead of) saving it: a PDF in the browser's viewer, an image,
 *  or text (an EDI file one segment per line, JSON indented) — then Download or Print it.
 *  A zip and other files say they can only be downloaded. */
export function FileViewer({ source, onClose }: { source: FileSource | null; onClose: () => void }) {
  const [ready, setReady] = useState<Ready | null>(null)
  const frame = useRef<HTMLDivElement>(null)
  const print = () => {
    if (!ready || !source) return
    if (ready.kind === 'pdf') {
      const f = frame.current?.querySelector('iframe')
      f?.contentWindow?.focus()
      f?.contentWindow?.print()
    } else if (ready.kind === 'image') {
      printFrame(`<img src="${ready.url}" style="max-width:100%">`, source.title)
    } else if (ready.kind === 'text') {
      const shown = frame.current?.querySelector('pre')?.textContent ?? ready.raw ?? ''
      printFrame(`<pre style="font:10px/1.4 monospace;white-space:pre-wrap;word-break:break-all">${esc(shown)}</pre>`, source.title)
    }
  }
  return (
    <Dialog
      open={source !== null}
      onOpenChange={(o) => { if (!o) onClose() }}
      title={source?.title ?? ''}
      description={ready?.file.name}
      className="max-w-5xl"
      footer={(
        <>
          {ready && ready.kind !== 'other' && (
            <Button variant="secondary" onClick={print}><Printer className="mr-1 h-4 w-4" aria-hidden />Print</Button>
          )}
          {ready && (ready.kind === 'pdf' || ready.kind === 'image') && (
            <Button variant="secondary" onClick={() => window.open(ready.url, '_blank', 'noopener')}>
              <ExternalLink className="mr-1 h-4 w-4" aria-hidden />Open in a new tab
            </Button>
          )}
          <Button disabled={!ready} onClick={() => ready && saveFile(ready.file)}><Download className="mr-1 h-4 w-4" aria-hidden />Download</Button>
        </>
      )}
    >
      <div ref={frame}>{source && <Body key={source.key} source={source} onReady={setReady} />}</div>
    </Dialog>
  )
}

/** A file's button: opens it in the viewer (look first, then Download or Print there). */
export function ViewFileButton({ source: file, children, size = 'sm', variant = 'secondary', ...rest }:
  { source: FileSource; children: ReactNode } & Omit<ButtonProps, 'onClick' | 'children'>) {
  const [source, setSource] = useState<FileSource | null>(null)
  return (
    <>
      <Button type="button" size={size} variant={variant} {...rest} onClick={() => setSource(file)}>
        <Eye className="mr-1 h-4 w-4" aria-hidden />{children}
      </Button>
      <FileViewer source={source} onClose={() => setSource(null)} />
    </>
  )
}
