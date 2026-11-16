import { errorMessage } from './submitFailure'

/**
 * Runs the action behind an in-page confirmation (ConfirmDialog) and turns
 * the outcome into what the dialog shows: null when it went through (the
 * dialog closes), or the refusal's message when it threw (the dialog stays
 * open and shows it, instead of the browser's alert()).
 */
export async function runConfirmAction(
  action: () => unknown,
  fallback = 'The action failed',
): Promise<string | null> {
  try {
    await action()
    return null
  } catch (err) {
    return errorMessage(err, fallback)
  }
}
