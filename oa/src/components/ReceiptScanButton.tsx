import { useRef, useState } from 'react'
import { ScanLine, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, ApiError } from '@/lib/api'

// Receipt OCR result — matches expense-api ocr_service.extract_receipt output.
export interface ReceiptFields {
  vendor_name:  string | null
  date:         string | null
  description:  string | null
  total_amount: number | null
  tax_amount:   number | null
  currency:     string | null
}

/**
 * Per-line "Scan Receipt" control. Uploads an image/PDF to the server-side OCR
 * endpoint (POST /api/v1/ocr/receipt) and hands the extracted fields back to the
 * caller for pre-fill. Degrades gracefully — the user can still enter the line
 * manually (OCR-003). A failed or empty read is said in visible text next to the
 * button: it used to live only in the button's tooltip, and a read that came
 * back 200 with every field empty (a blank image) said nothing at all (ISS-0218).
 *
 * `onFile` receives the picked file itself, before the scan runs and whether or
 * not it succeeds, so the caller can keep it as the line's receipt attachment:
 * a receipt that could not be read is still the receipt.
 */
export function ReceiptScanButton({
  onScanned, onFile, className,
}: {
  onScanned: (fields: ReceiptFields) => void
  onFile?: (file: File) => void
  className?: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const handle = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    onFile?.(file)
    setLoading(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await api.postForm<ReceiptFields>('/api/v1/ocr/receipt', form)
      if (isEmptyRead(r)) {
        // Nothing to pre-fill; leave the line as it is.
        setErr('Nothing could be read from this file. Enter the line by hand; the file stays attached.')
        return
      }
      onScanned(r)
    } catch (ex) {
      // OCR-003: an outage and an unreadable document read differently.
      setErr(ex instanceof ApiError && ex.status === 503
        ? 'Receipt reading is unavailable right now. Enter the line by hand.'
        : ex instanceof Error ? ex.message : 'Scan failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <span className={cn('inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5', className)}>
      <button
        type="button"
        disabled={loading}
        title={err || 'Scan a receipt image/PDF to auto-fill this line'}
        onClick={() => ref.current?.click()}
        className={cn(
          'inline-flex items-center gap-1 rounded border px-1.5 py-1 text-[10px] font-medium transition-colors',
          err
            ? 'border-danger-200 text-danger-500 hover:bg-danger-50'
            : 'border-neutral-200 text-neutral-500 hover:text-primary-700 hover:border-primary-300',
          loading && 'opacity-60',
        )}
      >
        {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <ScanLine className="h-3 w-3" />}
        {loading ? 'Scanning…' : 'Scan'}
      </button>
      <input
        ref={ref}
        type="file"
        accept="image/*,.pdf"
        className="hidden"
        onChange={handle}
      />
      {err && !loading && (
        <span role="alert" className="text-[10px] leading-tight text-danger-600">{err}</span>
      )}
    </span>
  )
}

/** True when the reader answered but found nothing usable on the page. */
function isEmptyRead(r: ReceiptFields | null | undefined): boolean {
  if (!r) return true
  return r.total_amount == null && r.tax_amount == null
    && !r.date && !(r.description ?? '').trim() && !(r.vendor_name ?? '').trim()
}
