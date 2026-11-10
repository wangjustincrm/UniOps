import { Download } from 'lucide-react'
import { ActionButton, type ActionButtonProps } from './ActionButton'

/** A file to download (2026-11-10): an ActionButton with the download icon. It cannot fire twice,
 *  shows it is busy, and a failure reads "Download failed: …" in the status row of its list or sheet. */
export function DownloadButton({ children, size = 'sm', variant = 'secondary', failPrefix = 'Download failed', ...rest }: ActionButtonProps) {
  return (
    <ActionButton size={size} variant={variant} failPrefix={failPrefix} {...rest}>
      <Download className="mr-1 h-4 w-4" aria-hidden />{children}
    </ActionButton>
  )
}
