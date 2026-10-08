/**
 * Shared shape for the Approval Timeline's "Send reminder" action.
 *
 * PR and PO each expose their own `POST /{doc}/{id}/remind`, but the response
 * is identical, so both services return this. `recipients` is what the UI shows
 * back to the clicker — the server resolves it with the notification
 * dispatcher's own rules (delegation stand-ins, role pools, shared mailboxes),
 * so it names who the email really went to.
 */
export interface ReminderResponse {
  sent: boolean
  document_number: string
  recipients: string[]
  /** ISO timestamp; another reminder for this step is refused until then. */
  next_allowed_at: string
}
