import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  User, Mail, Shield, KeyRound, ShieldCheck, ShieldOff,
  Eye, EyeOff, Save, ArrowLeft, Loader2, CheckCircle2, AlertTriangle,
  Building2, AtSign, Bell, PenLine, Upload, Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth.store'
import { authService, type ApiUser } from '@/services/auth'
import { api } from '@/lib/api'
import { SignaturePad, type SignaturePadHandle } from '@/components/shared/SignaturePad'

const ROLE_LABELS: Record<string, string> = {
  system_admin: 'System Admin',
  dept_manager: 'Department Manager',
  gm: 'General Manager',
  opm: 'Operations Manager',
  procurement_officer: 'Procurement Officer',
  procurement_manager: 'Procurement Manager',
  ap_clerk: 'AP Clerk',
  finance_bp: 'Finance Business Partner',
  finance_manager: 'Finance Manager',
  warehouse_staff: 'Warehouse Staff',
  requester: 'Requester',
  vendor_manager: 'Vendor Manager',
}

// ─── Section card wrapper ───────────────────────────────────────────────────

function SectionCard({ title, icon, children }: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
      <div className="flex items-center gap-3 px-6 py-4 border-b border-neutral-100">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          {icon}
        </div>
        <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  )
}

// ─── Personal Info Section ──────────────────────────────────────────────────

const NOTIFICATION_CHANNEL_LABELS: Record<string, string> = {
  email_only: 'Email only',
  teams_only: 'Microsoft Teams only',
  both: 'Email + Teams',
  none: 'None (in-app only)',
}

function PersonalInfoSection({ profile }: { profile: ApiUser }) {
  const queryClient = useQueryClient()
  const { updateUser } = useAuthStore()
  const [fullName, setFullName] = useState(profile.full_name)
  const [teamsAccount, setTeamsAccount] = useState(profile.teams_account ?? '')
  const [notifChannel, setNotifChannel] = useState(profile.notification_channel ?? 'email_only')
  const [saved, setSaved] = useState(false)

  const mutation = useMutation({
    mutationFn: (body: { full_name?: string; teams_account?: string; notification_channel?: string }) =>
      api.patch<ApiUser>('/auth/me', body),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      updateUser({ name: data.full_name })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  })

  const isDirty =
    fullName !== profile.full_name ||
    teamsAccount !== (profile.teams_account ?? '') ||
    notifChannel !== (profile.notification_channel ?? 'email_only')

  const inputCls = 'h-10 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* Full name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Display Name</label>
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className={inputCls}
            placeholder="Your full name"
          />
        </div>

        {/* Email (read-only) */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Email Address</label>
          <div className="h-10 flex items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 text-sm text-neutral-500">
            <Mail className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
            {profile.email}
          </div>
          <p className="text-xs text-neutral-400">Email cannot be changed. Contact your admin.</p>
        </div>

        {/* Role (read-only) */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Role</label>
          <div className="h-10 flex items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 text-sm text-neutral-500">
            <Building2 className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
            {ROLE_LABELS[profile.role] ?? profile.role}
          </div>
        </div>

        {/* Teams account */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">
            Microsoft Teams Account <span className="font-normal text-neutral-400">(optional)</span>
          </label>
          <div className="relative">
            <AtSign className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-neutral-400" />
            <input
              value={teamsAccount}
              onChange={(e) => setTeamsAccount(e.target.value)}
              className={cn(inputCls, 'pl-8')}
              placeholder="user@company.com"
              type="email"
            />
          </div>
          <p className="text-xs text-neutral-400">Used for Teams approval notifications when configured.</p>
        </div>

        {/* Notification channel */}
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <label className="text-xs font-medium text-neutral-700 flex items-center gap-1.5">
            <Bell className="h-3.5 w-3.5 text-neutral-400" />
            Notification Channel
          </label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(NOTIFICATION_CHANNEL_LABELS).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setNotifChannel(value)}
                className={cn(
                  'rounded-lg border px-3 py-2.5 text-xs font-medium text-left transition-colors',
                  notifChannel === value
                    ? 'border-primary-600 bg-primary-50 text-primary-700'
                    : 'border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-neutral-400">
            How you receive approval reminders and status updates. "None" hides outbound notifications — tasks still appear in your inbox.
          </p>
        </div>
      </div>

      {mutation.error && (
        <p className="text-xs text-danger-600 bg-danger-50 rounded-lg px-3 py-2">
          {mutation.error instanceof Error ? mutation.error.message : 'Save failed'}
        </p>
      )}

      <div className="flex items-center justify-end gap-3">
        {saved && (
          <span className="flex items-center gap-1 text-xs text-success-600">
            <CheckCircle2 className="h-3.5 w-3.5" /> Saved
          </span>
        )}
        <Button
          size="sm"
          disabled={!isDirty || mutation.isPending}
          onClick={() => mutation.mutate({
            full_name: fullName,
            teams_account: teamsAccount || undefined,
            notification_channel: notifChannel,
          })}
        >
          {mutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Save Changes
        </Button>
      </div>
    </div>
  )
}

// ─── Change Password Section ────────────────────────────────────────────────

// Kept comfortably under identity's MAX_SIGNATURE_CHARS (400k characters of
// base64): a photographed signature is the only realistic way to exceed it, and
// the pad's own output is a few tens of KB.
const MAX_UPLOAD_BYTES = 200 * 1024

function SignatureSection({ profile }: { profile: ApiUser }) {
  const queryClient = useQueryClient()
  const padRef = useRef<SignaturePadHandle>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<'draw' | 'upload'>('draw')
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [padEmpty, setPadEmpty] = useState(true)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const mutation = useMutation({
    // "" is how the API clears a signature — null would read as "not supplied".
    mutationFn: (signature_image: string) =>
      api.patch<ApiUser>('/auth/me', { signature_image }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      padRef.current?.clear()
      setUploaded(null)
      setError('')
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not save signature'),
  })

  const onPickFile = (file: File | undefined) => {
    setError('')
    if (!file) return
    if (!/^image\/(png|jpeg)$/.test(file.type)) {
      setError('Use a PNG or JPEG image.')
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('That image is too large — keep it under 200 KB.')
      return
    }
    const reader = new FileReader()
    reader.onload = () => setUploaded(String(reader.result))
    reader.onerror = () => setError('Could not read that file.')
    reader.readAsDataURL(file)
  }

  const handleSave = () => {
    setError('')
    const next = mode === 'draw' ? padRef.current?.toDataURL() : uploaded
    if (!next) {
      setError(mode === 'draw' ? 'Draw your signature first.' : 'Choose an image first.')
      return
    }
    mutation.mutate(next)
  }

  const canSave = mode === 'draw' ? !padEmpty : uploaded !== null

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-neutral-500">
        Used to sign documents in UniOps — a PO sign-off stamps this image onto
        the order. It is copied onto each document as you sign it, so changing it
        here never alters anything you have already signed.
      </p>

      {profile.signature_image ? (
        <div className="flex items-center gap-4 rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <img
            src={profile.signature_image}
            alt="Your saved signature"
            className="h-14 max-w-[240px] object-contain"
          />
          <div className="flex-1">
            <p className="text-xs font-medium text-neutral-700">Current signature</p>
            <p className="text-xs text-neutral-400">Draw or upload a new one below to replace it.</p>
          </div>
          <button
            type="button"
            onClick={() => mutation.mutate('')}
            disabled={mutation.isPending}
            className="flex items-center gap-1.5 text-xs font-medium text-neutral-500 hover:text-danger-600 disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" /> Remove
          </button>
        </div>
      ) : (
        <div className="flex items-start gap-2 rounded-xl border border-warning-200 bg-warning-50 px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-600" />
          <p className="text-xs text-warning-700">
            You have no signature yet. Anything waiting for your signature cannot
            be signed until you add one.
          </p>
        </div>
      )}

      <div className="flex gap-1 self-start rounded-lg bg-neutral-100 p-1">
        {([['draw', 'Draw', PenLine], ['upload', 'Upload', Upload]] as const).map(
          ([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => { setMode(value); setError('') }}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                mode === value ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-500 hover:text-neutral-700',
              )}
            >
              <Icon className="h-3.5 w-3.5" /> {label}
            </button>
          ),
        )}
      </div>

      {mode === 'draw' ? (
        <SignaturePad ref={padRef} onChange={setPadEmpty} />
      ) : (
        <div className="flex flex-col gap-3">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg"
            onChange={(e) => onPickFile(e.target.files?.[0])}
            className="block w-full text-xs text-neutral-500 file:mr-3 file:rounded-lg file:border-0 file:bg-primary-50 file:px-3 file:py-2 file:text-xs file:font-medium file:text-primary-700 hover:file:bg-primary-100"
          />
          {uploaded && (
            <div className="flex items-center gap-4 rounded-lg border border-dashed border-neutral-300 bg-white p-3">
              <img src={uploaded} alt="Signature to be saved" className="h-14 max-w-[240px] object-contain" />
              <button
                type="button"
                onClick={() => { setUploaded(null); if (fileRef.current) fileRef.current.value = '' }}
                className="text-xs font-medium text-neutral-500 hover:text-neutral-700"
              >
                Choose another
              </button>
            </div>
          )}
          <p className="text-xs text-neutral-400">
            PNG or JPEG under 200 KB. A PNG with a transparent background prints best.
          </p>
        </div>
      )}

      {error && (
        <p className="flex items-center gap-1.5 text-xs text-danger-600">
          <AlertTriangle className="h-3.5 w-3.5" /> {error}
        </p>
      )}

      <div className="flex items-center gap-3">
        <Button onClick={handleSave} disabled={!canSave || mutation.isPending} className="gap-2">
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save Signature
        </Button>
        {saved && (
          <span className="flex items-center gap-1.5 text-sm text-success-600">
            <CheckCircle2 className="h-4 w-4" /> Saved
          </span>
        )}
      </div>
    </div>
  )
}

function ChangePasswordSection() {
  const [currentPwd, setCurrentPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [success, setSuccess] = useState(false)

  const mutation = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api.post<void>('/auth/change-password', body),
    onSuccess: () => {
      setSuccess(true)
      setCurrentPwd(''); setNewPwd(''); setConfirmPwd('')
      setTimeout(() => setSuccess(false), 3000)
    },
    onError: (e) => {
      setErrors({ currentPwd: e instanceof Error ? e.message : 'Current password is incorrect' })
    },
  })

  const validate = () => {
    const e: Record<string, string> = {}
    if (!currentPwd) e.currentPwd = 'Required'
    if (!newPwd) e.newPwd = 'Required'
    else if (newPwd.length < 8) e.newPwd = 'Must be at least 8 characters'
    else if (newPwd === currentPwd) e.newPwd = 'New password must differ from current'
    if (!confirmPwd) e.confirmPwd = 'Required'
    else if (confirmPwd !== newPwd) e.confirmPwd = 'Passwords do not match'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const inputCls = (err?: string) => cn(
    'h-10 w-full rounded-lg border bg-white px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
    err ? 'border-danger-500' : 'border-neutral-300'
  )

  const ToggleBtn = ({ show, onToggle }: { show: boolean; onToggle: () => void }) => (
    <button type="button" onClick={onToggle}
      className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
      {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  )

  return (
    <div className="flex flex-col gap-4">
      {success && (
        <div className="flex items-center gap-2 rounded-lg bg-success-50 border border-success-200 px-4 py-3 text-sm text-success-700">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          Password changed successfully.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {/* Current */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Current Password *</label>
          <div className="relative">
            <input type={showCurrent ? 'text' : 'password'} value={currentPwd}
              onChange={(e) => { setCurrentPwd(e.target.value); setErrors((p) => ({ ...p, currentPwd: '' })) }}
              className={inputCls(errors.currentPwd)} placeholder="Current password" />
            <ToggleBtn show={showCurrent} onToggle={() => setShowCurrent((v) => !v)} />
          </div>
          {errors.currentPwd && <p className="text-xs text-danger-600">{errors.currentPwd}</p>}
        </div>

        {/* New */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">New Password *</label>
          <div className="relative">
            <input type={showNew ? 'text' : 'password'} value={newPwd}
              onChange={(e) => { setNewPwd(e.target.value); setErrors((p) => ({ ...p, newPwd: '' })) }}
              className={inputCls(errors.newPwd)} placeholder="Min. 8 characters" />
            <ToggleBtn show={showNew} onToggle={() => setShowNew((v) => !v)} />
          </div>
          {errors.newPwd && <p className="text-xs text-danger-600">{errors.newPwd}</p>}
        </div>

        {/* Confirm */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Confirm New Password *</label>
          <div className="relative">
            <input type={showConfirm ? 'text' : 'password'} value={confirmPwd}
              onChange={(e) => { setConfirmPwd(e.target.value); setErrors((p) => ({ ...p, confirmPwd: '' })) }}
              className={inputCls(errors.confirmPwd)} placeholder="Re-enter password" />
            <ToggleBtn show={showConfirm} onToggle={() => setShowConfirm((v) => !v)} />
          </div>
          {errors.confirmPwd && <p className="text-xs text-danger-600">{errors.confirmPwd}</p>}
        </div>
      </div>

      <div className="flex justify-end">
        <Button size="sm" disabled={mutation.isPending}
          onClick={() => { if (validate()) mutation.mutate({ current_password: currentPwd, new_password: newPwd }) }}>
          {mutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
          Change Password
        </Button>
      </div>
    </div>
  )
}

// ─── MFA Section ───────────────────────────────────────────────────────────

function MfaSection({ mfaEnabled }: { mfaEnabled: boolean }) {
  const queryClient = useQueryClient()
  const [msg, setMsg] = useState('')
  const [isEnabled, setIsEnabled] = useState(mfaEnabled)

  const mutation = useMutation({
    mutationFn: (enable: boolean) =>
      api.post<void>(enable ? '/auth/mfa/enable' : '/auth/mfa/disable'),
    onSuccess: (_data, enable) => {
      setIsEnabled(enable)
      setMsg(enable ? 'MFA enabled. A verification code will be sent to your email at next login.' : 'MFA disabled.')
      queryClient.invalidateQueries({ queryKey: ['me'] })
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : 'Action failed'),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-5 py-4">
        <div className="flex items-center gap-3">
          {isEnabled
            ? <ShieldCheck className="h-5 w-5 text-success-600" />
            : <ShieldOff className="h-5 w-5 text-neutral-400" />}
          <div>
            <p className="text-sm font-medium text-neutral-900">
              Email MFA — {isEnabled ? 'Enabled' : 'Disabled'}
            </p>
            <p className="text-xs text-neutral-500 mt-0.5">
              {isEnabled
                ? 'A verification code is sent to your email on each sign-in.'
                : 'No MFA challenge at sign-in. Enable for extra security.'}
            </p>
          </div>
        </div>
        <button
          onClick={() => mutation.mutate(!isEnabled)}
          disabled={mutation.isPending}
          className={cn(
            'relative h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none disabled:opacity-50',
            isEnabled ? 'bg-primary-600' : 'bg-neutral-300'
          )}
          aria-label={isEnabled ? 'Disable MFA' : 'Enable MFA'}
        >
          <span className={cn(
            'absolute top-1 left-1 h-4 w-4 rounded-full bg-white shadow transition-transform',
            isEnabled ? 'translate-x-5' : 'translate-x-0'
          )} />
        </button>
      </div>

      {msg && (
        <p className="text-xs text-neutral-600 bg-neutral-50 rounded-lg border border-neutral-200 px-3 py-2">{msg}</p>
      )}

      <div className="rounded-lg border border-warning-200 bg-warning-50 px-4 py-3 flex items-start gap-2.5">
        <AlertTriangle className="h-4 w-4 text-warning-600 shrink-0 mt-0.5" />
        <p className="text-xs text-warning-700">
          MFA may be enforced company-wide by your System Admin regardless of this setting.
          Contact your admin if you cannot disable MFA.
        </p>
      </div>
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────

export default function ProfilePage() {
  const navigate = useNavigate()
  const { user: authUser } = useAuthStore()

  const { data: profile, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: () => authService.me() as Promise<ApiUser & { teams_account?: string; mfa_enabled: boolean }>,
  })

  const initials = authUser?.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2) ?? '?'

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    )
  }

  if (!profile) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <p className="text-sm text-neutral-400">Could not load profile. Please refresh.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="rounded-lg border border-neutral-200 bg-white px-6 py-4">
        <button onClick={() => navigate(-1)} className="mb-3 flex items-center gap-1.5 text-sm text-neutral-500 hover:text-primary-600">
          <ArrowLeft className="h-4 w-4" />
          Back
        </button>
        <div className="flex items-center gap-4">
          {/* Avatar */}
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary-100 text-xl font-bold text-primary-700 shrink-0">
            {initials}
          </div>
          <div>
            <h1 className="text-xl font-bold text-neutral-900">{profile.full_name}</h1>
            <p className="text-sm text-neutral-500">{ROLE_LABELS[profile.role] ?? profile.role} · {profile.email}</p>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-col gap-5">
        <SectionCard title="Personal Information" icon={<User className="h-4 w-4" />}>
          <PersonalInfoSection profile={profile} />
        </SectionCard>

        <SectionCard title="My Signature" icon={<PenLine className="h-4 w-4" />}>
          <SignatureSection profile={profile} />
        </SectionCard>

        <SectionCard title="Change Password" icon={<KeyRound className="h-4 w-4" />}>
          <ChangePasswordSection />
        </SectionCard>

        <SectionCard title="Multi-Factor Authentication" icon={<Shield className="h-4 w-4" />}>
          <MfaSection mfaEnabled={profile.mfa_enabled} />
        </SectionCard>
      </div>
    </div>
  )
}
