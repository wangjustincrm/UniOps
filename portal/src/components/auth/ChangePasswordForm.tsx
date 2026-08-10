/**
 * ChangePasswordForm — the shared password-change form.
 *
 * One implementation, two chromes:
 *  - TopHeader's voluntary "Change Password" modal (dismissible, has Cancel)
 *  - ForcePasswordChangePage — the blocking gate rendered whenever the account
 *    carries `must_change_password` (admin-created account signing in for the
 *    first time, an admin password reset, or a password past the expiry window
 *    configured in Admin → Security).
 *
 * Hits the EPMS-API endpoint (a thin proxy to identity-api) with the stored
 * bearer token. identity-api clears must_change_password and stamps
 * password_changed_at on success, which restarts the expiry clock.
 */
import { useState } from 'react'
import { Eye, EyeOff, KeyRound, CheckCircle2 } from 'lucide-react'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export function PwdField({
  label, value, onChange, show, onToggle, error, placeholder, autoFocus = false,
}: {
  label: string; value: string; onChange: (v: string) => void
  show: boolean; onToggle: () => void; error?: string; placeholder: string; autoFocus?: boolean
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-neutral-700">{label} <span className="text-red-500">*</span></label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'h-10 w-full rounded-lg border bg-white px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-colors',
            error ? 'border-red-500' : 'border-neutral-300',
          )}
          placeholder={placeholder}
          autoFocus={autoFocus}
        />
        <button type="button" onClick={onToggle}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

export function ChangePasswordForm({ onSuccess, onCancel }: {
  /** Fired ~1.5s after the API accepts the change, once the success state has been shown. */
  onSuccess: () => void
  /** Omit to hide the Cancel button — forced mode has no way out but signing out. */
  onCancel?: () => void
}) {
  const [currentPwd, setCurrentPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState(false)

  const clearErr = (key: string) => setErrors((p) => ({ ...p, [key]: '' }))

  const validate = (): boolean => {
    const e: Record<string, string> = {}
    if (!currentPwd) e.currentPwd = 'Required'
    if (!newPwd) e.newPwd = 'Required'
    else if (newPwd.length < 8) e.newPwd = 'Must be at least 8 characters'
    else if (newPwd === currentPwd) e.newPwd = 'New password must differ from current password'
    if (!confirmPwd) e.confirmPwd = 'Required'
    else if (confirmPwd !== newPwd) e.confirmPwd = 'Passwords do not match'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async () => {
    if (!validate()) return
    setSubmitting(true)
    try {
      await epmsApi.post('/auth/change-password', { current_password: currentPwd, new_password: newPwd })
      setSuccess(true)
      setTimeout(onSuccess, 1500)
    } catch (err) {
      setErrors((e) => ({ ...e, currentPwd: err instanceof Error ? err.message : 'Current password is incorrect' }))
    } finally {
      setSubmitting(false)
    }
  }

  if (success) {
    return (
      <div className="flex flex-col items-center gap-3 py-6 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-50">
          <CheckCircle2 className="h-6 w-6 text-green-600" />
        </div>
        <p className="text-sm font-medium text-neutral-900">Password changed successfully</p>
      </div>
    )
  }

  return (
    <>
      <PwdField label="Current Password" value={currentPwd}
        onChange={(v) => { setCurrentPwd(v); clearErr('currentPwd') }}
        show={showCurrent} onToggle={() => setShowCurrent((v) => !v)}
        error={errors.currentPwd} placeholder="Enter current password" autoFocus />
      <PwdField label="New Password" value={newPwd}
        onChange={(v) => { setNewPwd(v); clearErr('newPwd') }}
        show={showNew} onToggle={() => setShowNew((v) => !v)}
        error={errors.newPwd} placeholder="Min. 8 characters" />
      <PwdField label="Confirm New Password" value={confirmPwd}
        onChange={(v) => { setConfirmPwd(v); clearErr('confirmPwd') }}
        show={showConfirm} onToggle={() => setShowConfirm((v) => !v)}
        error={errors.confirmPwd} placeholder="Re-enter new password" />
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button onClick={onCancel}
            className="rounded-lg px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100">
            Cancel
          </button>
        )}
        <button onClick={handleSubmit} disabled={submitting}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50">
          <KeyRound className="h-3.5 w-3.5" />
          {submitting ? 'Saving…' : 'Change Password'}
        </button>
      </div>
    </>
  )
}
