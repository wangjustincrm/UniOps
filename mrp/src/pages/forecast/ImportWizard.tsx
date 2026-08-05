// Import wizard: pick a file -> validate (dry_run=true) -> show the
// validation report -> user confirms -> apply (dry_run=false). Bad rows are
// listed individually (row/column/reason) and never block the good rows —
// see forecast.py's import_forecast() docstring: dry_run always runs every
// check so the preview accurately reflects what dry_run=false will do.
import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Upload, Loader2, X as XIcon, AlertTriangle, CheckCircle2, FileSpreadsheet } from 'lucide-react'
import { Button, Badge } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { forecastApi, type ImportResponse } from './forecastApi'

type Phase = 'pick' | 'validating' | 'report' | 'importing'

export function ImportWizard({
  versionId, onClose, onImported, notifySuccess,
}: {
  versionId: string
  onClose: () => void
  onImported: () => void
  notifySuccess: (message: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<Phase>('pick')
  const [report, setReport] = useState<ImportResponse | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  async function runValidation(f: File) {
    setFile(f)
    setErrorMsg(null)
    setReport(null)
    setPhase('validating')
    try {
      const res = await forecastApi.importForecast(versionId, f, true)
      setReport(res)
      setPhase('report')
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : 'Validation failed — please retry.')
      setPhase('pick')
    }
  }

  function onFilePick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    e.target.value = ''
    if (f) void runValidation(f)
  }

  async function confirmImport() {
    if (!file) return
    setPhase('importing')
    setErrorMsg(null)
    try {
      const res = await forecastApi.importForecast(versionId, file, false)
      const parts = [`${res.would_upsert} cell(s) imported`]
      if (res.skipped_frozen.length > 0) parts.push(`${res.skipped_frozen.length} skipped (frozen)`)
      if (res.error_rows.length > 0) parts.push(`${res.error_rows.length} row(s) skipped (errors)`)
      notifySuccess(parts.join(', ') + '.')
      onImported()
      onClose()
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : 'Import failed — please retry.')
      setPhase('report')
    }
  }

  const busy = phase === 'validating' || phase === 'importing'

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-xl rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <h2 className="text-base font-semibold text-neutral-900">Import Forecast from Excel</h2>
          <button type="button" onClick={onClose} aria-label="Close" disabled={busy} className="text-neutral-400 hover:text-neutral-600 disabled:opacity-40">
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-600">
            Columns: <span className="font-mono">Material Code</span>, <span className="font-mono">Name</span>, then one column per month (<span className="font-mono">YYYY-MM</span>). Use <strong>Download Template</strong> to get the exact headers for this version.
          </div>

          <div>
            <input ref={inputRef} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden onChange={onFilePick} />
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={busy}
              className="w-full flex flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 px-4 py-6 text-sm text-neutral-500 hover:border-primary-400 hover:text-primary-700 transition-colors disabled:opacity-60"
            >
              <FileSpreadsheet className="h-8 w-8 text-neutral-300" />
              {file ? <span className="font-medium text-neutral-700">{file.name}</span> : <span>Click to select an .xlsx file</span>}
            </button>
          </div>

          {phase === 'validating' && (
            <div className="flex items-center gap-2 text-sm text-neutral-600">
              <Loader2 className="h-4 w-4 animate-spin" /> Validating…
            </div>
          )}

          {errorMsg && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          {report && phase !== 'validating' && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={report.ok_rows > 0 ? 'success' : 'neutral'}>{report.ok_rows} row(s) valid</Badge>
                {report.error_rows.length > 0 && <Badge variant="danger">{report.error_rows.length} row(s) with errors</Badge>}
                {report.skipped_frozen.length > 0 && <Badge variant="warning">{report.skipped_frozen.length} frozen (will be skipped)</Badge>}
                <Badge variant="info">{report.would_upsert} cell(s) will be written</Badge>
              </div>

              {report.error_rows.length === 0 ? (
                <p className="flex items-center gap-1.5 text-sm text-success-700">
                  <CheckCircle2 className="h-4 w-4" /> No validation errors.
                </p>
              ) : (
                <div role="alert" className="rounded-md border border-danger-200 bg-danger-50 p-3">
                  <p className="mb-1.5 text-xs font-semibold text-danger-700">
                    {report.error_rows.length} row(s) failed validation — these are skipped, the rest import normally:
                  </p>
                  <ul className="max-h-48 space-y-0.5 overflow-y-auto text-xs text-danger-700">
                    {report.error_rows.map((e, i) => (
                      <li key={i}>
                        {e.row === 0 ? 'Header' : `Row ${e.row}`}
                        {e.column ? `, column "${e.column}"` : ''}: {e.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          {(phase === 'report' || phase === 'importing') && report && (
            <Button type="button" size="sm" onClick={confirmImport} disabled={busy || report.ok_rows === 0}>
              {phase === 'importing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              Import {report.would_upsert} cell{report.would_upsert === 1 ? '' : 's'}
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
