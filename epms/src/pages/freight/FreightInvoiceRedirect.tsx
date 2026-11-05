import { useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'

/** Courier invoices moved to the Invoices page (/invoices/:id). Old links and
 *  tabs restored from a saved session still point here; swap this tab for the
 *  invoice's own. */
export default function FreightInvoiceRedirect() {
  const { id = '' } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  useEffect(() => { if (id) replaceTab(`/invoices/${id}`) }, [id]) // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="flex justify-center py-24"><Loader2 className="h-6 w-6 animate-spin text-neutral-300" /></div>
}
