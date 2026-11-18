import { AlertTriangle } from 'lucide-react'

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-danger-soft border border-danger-line px-3 py-2 text-sm text-danger-fg">
      <AlertTriangle className="h-4 w-4 shrink-0" />{message}
    </div>
  )
}
