import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import type { ReminderState } from '@/components/pr/ApprovalTimeline'
import type { ReminderResponse } from '@/services/reminder'

/**
 * Drives the Approval Timeline's "Send reminder" link.
 *
 * EPMS has no global toast, and this action changes nothing on the page, so the
 * result has to be rendered next to the button or the click looks like it did
 * nothing — which is exactly how the button behaved while it was still wired to
 * `console.log`. The server answers 409 (nobody to remind / unreachable) and
 * 429 (already reminded today) with a sentence written for the user, so those
 * are shown verbatim rather than flattened into "failed".
 */
export function useApprovalReminder(send: () => Promise<ReminderResponse>) {
  const [state, setState] = useState<ReminderState>({ status: 'idle' })

  const mutation = useMutation({
    mutationFn: send,
    onMutate: () => setState({ status: 'pending' }),
    onSuccess: (res) =>
      setState({
        status: 'success',
        message: `Reminder sent to ${res.recipients.join(', ')}.`,
      }),
    onError: (err) =>
      setState({
        status: 'error',
        message: err instanceof Error ? err.message : 'Could not send the reminder.',
      }),
  })

  return { reminder: state, sendReminder: () => mutation.mutate() }
}
