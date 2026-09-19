// One configured pdf.js for the whole app.
//
// The worker path has to be set exactly once per bundle, and it was living in
// FilePreviewPanel — which meant the second module to want pdf.js (the page
// splitter) would either re-assign it or silently depend on the preview having
// been imported first. Both are the kind of thing that works in dev and breaks
// in a build where the import order differs.
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).href

export { pdfjsLib }
export type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
