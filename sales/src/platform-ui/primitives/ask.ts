/** A yes/no question in the app's own dialog (2026-11-10), from anywhere, without state of its own:
 *  `if (await ask({ title: 'Discard?' })) …`. <AskHost> (mounted once, in the app) shows it; with
 *  no host (a test, an embedded page) it falls back to the browser's confirm. */
export interface AskSpec {
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
}

type Host = (spec: AskSpec) => Promise<boolean>
let host: Host | null = null

/** Called by <AskHost>; returns the unregister. */
export function registerAskHost(h: Host): () => void {
  host = h
  return () => { if (host === h) host = null }
}

export function ask(spec: AskSpec): Promise<boolean> {
  if (host) return host(spec)
  return Promise.resolve(window.confirm(spec.description ? `${spec.title}\n\n${spec.description}` : spec.title))
}
