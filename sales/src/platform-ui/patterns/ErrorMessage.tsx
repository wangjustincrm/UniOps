import { ErrorLine, ErrorState } from '../primitives/States'
import { errorText } from '../resource/businessError'

/** Inline error line for an error of any kind (a BusinessError's message,
 *  or the caller's text for its code). */
export function ErrorMessage({ error, messages }: { error: unknown; messages?: Record<string, string> }) {
  return <ErrorLine message={error ? errorText(error, messages) : null} />
}

/** A failed load, with the error's text and an optional retry. */
export function ErrorView({ error, messages, onRetry, className }: {
  error: unknown
  messages?: Record<string, string>
  onRetry?: () => void
  className?: string
}) {
  return <ErrorState message={errorText(error, messages)} onRetry={onRetry} className={className} />
}
