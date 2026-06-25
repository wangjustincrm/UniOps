import { useRef, useState } from 'react'
import { ScanLine, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'

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
 * caller for pre-fill. Degrades gracefully — failures surface as a tooltip and
 * the user can still enter the line manually (OCR-006).
 */
export function ReceiptScanButton({
  onScanned, className,
}: {
  onScanned: (fields: ReceiptFields) => void
  className?: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const handle = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setLoading(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await api.postForm<ReceiptFields>('/api/v1/ocr/receipt', form)
      onScanned(r)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Scan failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={loading}
        title={err || 'Scan a receipt image/PDF to auto-fill this line'}
        onClick={() => ref.current?.click()}
        className={cn(
          'inline-flex items-center gap-1 rounded border px-1.5 py-1 text-[10px] font-medium transition-colors',
          err
            ? 'border-red-200 text-red-500 hover:bg-red-50'
            : 'border-neutral-200 text-neutral-500 hover:text-primary-700 hover:border-primary-300',
          loading && 'opacity-60',
          className,
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
    </>
  )
}
