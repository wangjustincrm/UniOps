import { useState, type ReactNode } from 'react'
import { FileViewer, type FileSource } from './FileViewer'

/** One viewer for a page or a sheet: `view(source)` opens it; render `viewer` once. */
export function useFileViewer(): { view: (s: FileSource) => void; viewer: ReactNode } {
  const [source, setSource] = useState<FileSource | null>(null)
  return { view: setSource, viewer: <FileViewer source={source} onClose={() => setSource(null)} /> }
}
