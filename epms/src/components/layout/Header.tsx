import { useLocation, Link, useNavigate } from 'react-router-dom'
import { Bell, User, Settings, KeyRound, Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { Alert, FormField, IconButton, Input, Modal, Topbar, UserMenu } from '@uniops/shell'
import { useQueryClient } from '@tanstack/react-query'
import { useTasks } from '@/hooks/useTasks'
import { isEpmsInboxTask } from '@/lib/taskTypes'
import { useAuthStore } from '@/stores/auth.store'
import { api } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { Button } from '@/components/ui/button'

const BREADCRUMB_LABELS: Record<string, string> = {
  dashboard: 'Dashboard',
  tasks: 'Task Inbox',
  pr: 'Purchase Requisitions',
  po: 'Purchase Orders',
  new: 'New',
  gr: 'Goods Receipt',
  invoices: 'Invoices',
  'vendor-credits': 'Vendor Credits',
  pa: 'Payment Applications',
  budget: 'Budget',
  reports: 'Reports',
  profile: 'My Profile',
  vendors: 'Vendors',
  projects: 'Projects',
  parts: 'Parts Catalog',
  admin: 'Admin Panel',
  create: 'Create',
  edit: 'Edit',
  collect: 'Collection',
  report: 'Receiving Report',
  settle: 'Settlement',
  freight: 'Freight',
  agreements: 'Agreements',
  receipts: 'Agreement Receipts',
  'service-confirm': 'Confirm Service Completion',
  'edit-imported': 'Edit Details',
  catalog: 'Account Catalog',
  plans: 'Budget Plans',
}

const DETAIL_LABELS: Record<string, string> = {
  pr: 'PR Detail',
  po: 'PO Detail',
  gr: 'GR Detail',
  invoices: 'Invoice Detail',
  pa: 'PA Detail',
  vendors: 'Vendor',
  projects: 'Project',
  parts: 'Part',
  agreements: 'Agreement Detail',
  receipts: 'Receipt Detail',
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const QUERY_KEY_MAP: Record<string, string> = {
  pr: 'pr', po: 'po', gr: 'gr', invoices: 'invoice', pa: 'pa',
  vendors: 'vendor', projects: 'project', parts: 'part',
}

function useBreadcrumbs() {
  const { pathname } = useLocation()
  const queryClient = useQueryClient()
  const parts = pathname.split('/').filter(Boolean)
  const crumbs = [{ label: 'Home', href: '/dashboard' }]
  let path = ''
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]
    path += `/${part}`
    if (path === '/dashboard') continue
    let label: string
    if (UUID_RE.test(part)) {
      const parentSegment = parts[i - 1]
      const queryKey = QUERY_KEY_MAP[parentSegment]
      const cached = queryKey
        ? (queryClient.getQueryData([queryKey, part]) as { number?: string } | undefined)
        : undefined
      label = cached?.number ?? (DETAIL_LABELS[parentSegment] ?? 'Detail')
    } else {
      label = BREADCRUMB_LABELS[part] ?? part
    }
    crumbs.push({ label, href: path })
  }
  return crumbs
}

// ─── Change Password Modal ────────────────────────────────────────────────────

interface ChangePasswordModalProps {
  forced?: boolean
  forceReason?: string
  onClose: () => void
}

function PasswordField({ id, label, value, onChange, error, placeholder, autoFocus }: {
  id: string; label: string; value: string; onChange: (v: string) => void
  error?: string; placeholder?: string; autoFocus?: boolean
}) {
  const [show, setShow] = useState(false)
  return (
    <FormField label={label} required htmlFor={id} error={error || undefined}>
      <div className="relative">
        <Input id={id} type={show ? 'text' : 'password'} value={value} error={!!error} placeholder={placeholder}
          autoFocus={autoFocus} className="pr-11" onChange={(e) => onChange(e.target.value)} />
        <IconButton type="button" label={show ? 'Hide password' : 'Show password'} className="absolute right-1 top-1"
          icon={show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />} onClick={() => setShow((v) => !v)} />
      </div>
    </FormField>
  )
}

function ChangePasswordModal({ forced = false, forceReason, onClose }: Omit<ChangePasswordModalProps, 'userId'>) {
  const { user } = useAuthStore()

  const [currentPwd, setCurrentPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState(false)

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
      await api.post('/auth/change-password', { current_password: currentPwd, new_password: newPwd })
      setSuccess(true)
      setTimeout(() => onClose(), 1500)
    } catch (err) {
      setErrors((e) => ({ ...e, currentPwd: err instanceof Error ? err.message : 'Current password is incorrect' }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!forced}
      busy={submitting}
      // Closed only by its buttons, as before: Escape must not drop what was typed.
      closeOnEscape={false}
      size="sm"
      icon={<KeyRound />}
      title="Change Password"
      description={user ? `${user.name} · ${user.email}` : undefined}
      bodyClassName="flex flex-col gap-4"
      footer={success ? undefined : <>
        {!forced && <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>}
        <Button size="sm" onClick={handleSubmit} loading={submitting}>
          <KeyRound className="h-3.5 w-3.5" />
          {submitting ? 'Saving…' : 'Change Password'}
        </Button>
      </>}
    >
      {forced && forceReason && <Alert tone="warning">{forceReason}</Alert>}
      {success ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <KeyRound className="h-6 w-6 text-success-fg" />
          <p className="text-sm font-medium text-fg">Password changed successfully</p>
          <p className="text-xs text-fg-muted">Redirecting…</p>
        </div>
      ) : (
        <>
          <PasswordField id="pwd-current" label="Current Password" value={currentPwd} autoFocus
            placeholder="Enter current password" error={errors.currentPwd}
            onChange={(v) => { setCurrentPwd(v); setErrors((p) => ({ ...p, currentPwd: '' })) }} />
          <PasswordField id="pwd-new" label="New Password" value={newPwd} placeholder="Min. 8 characters"
            error={errors.newPwd} onChange={(v) => { setNewPwd(v); setErrors((p) => ({ ...p, newPwd: '' })) }} />
          <PasswordField id="pwd-confirm" label="Confirm New Password" value={confirmPwd} placeholder="Re-enter new password"
            error={errors.confirmPwd} onChange={(v) => { setConfirmPwd(v); setErrors((p) => ({ ...p, confirmPwd: '' })) }} />
        </>
      )}
    </Modal>
  )
}

// ─── Forced Change Password Modal (first-login / after admin reset) ──────────

export function ForcedChangePasswordModal({ onDone }: { onDone: () => void }) {
  return (
    <ChangePasswordModal
      forced
      forceReason="Your password was set by an administrator. You must choose a new password before continuing."
      onClose={onDone}
    />
  )
}

// ─── Header ───────────────────────────────────────────────────────────────────

interface HeaderProps {
  onMobileMenuToggle?: () => void
}

/** "procurement_officer" -> "Procurement officer" (what the old menu showed). */
function roleLabel(role?: string) {
  if (!role) return null
  const t = role.replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

export function Header({ onMobileMenuToggle }: HeaderProps) {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const breadcrumbs = useBreadcrumbs()
  const [showChangePwd, setShowChangePwd] = useState(false)
  // Same hook as the Task Inbox and every detail page — see useTasks for why
  // this must not be a hand-rolled useQuery on the same key.
  const { data: taskData } = useTasks({ is_completed: false }, { refetchInterval: 60_000 })
  // Counts what the Task Inbox it links to lists — EPMS work only.
  const notificationCount = (taskData?.items ?? []).filter(isEpmsInboxTask).length

  return (
    <>
      <Topbar
        onMobileMenu={onMobileMenuToggle}
        className="relative z-10"
        start={
          <nav aria-label="Breadcrumb">
            <ol className="flex items-center gap-1 text-sm">
              {breadcrumbs.map((crumb, i) => (
                <li key={crumb.href} className="flex items-center gap-1">
                  {i > 0 && <span className="text-fg-faint">/</span>}
                  {i === breadcrumbs.length - 1 ? (
                    <span className="font-medium text-fg">{crumb.label}</span>
                  ) : (
                    <Link to={crumb.href} className="text-fg-muted hover:text-accent-fg">
                      {crumb.label}
                    </Link>
                  )}
                </li>
              ))}
            </ol>
          </nav>
        }
        end={
          <>
            <span className="relative">
              <IconButton label={`${notificationCount} open tasks`} icon={<Bell className="h-5 w-5" />} size="icon"
                onClick={() => navigate('/tasks')} />
              {notificationCount > 0 && (
                <span aria-hidden className="pointer-events-none absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger-solid px-1 font-mono text-2xs text-fg-on-accent">
                  {notificationCount > 99 ? '99+' : notificationCount}
                </span>
              )}
            </span>
            <UserMenu
              name={user?.name ?? ''}
              subtitle={roleLabel(user?.role)}
              items={[
                { label: 'Profile', icon: <User />, onSelect: () => navigate('/profile') },
                ...(user?.role === 'system_admin' ? [{ label: 'Settings', icon: <Settings />, onSelect: () => navigate('/admin') }] : []),
                { label: 'Change Password', icon: <KeyRound />, onSelect: () => setShowChangePwd(true) },
              ]}
              onSignOut={globalSignOut}
            />
          </>
        }
      />

      {showChangePwd && <ChangePasswordModal onClose={() => setShowChangePwd(false)} />}
    </>
  )
}
