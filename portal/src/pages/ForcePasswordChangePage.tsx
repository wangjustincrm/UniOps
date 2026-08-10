/**
 * ForcePasswordChangePage — full-screen gate shown instead of any Portal page
 * while the signed-in account carries `must_change_password`.
 *
 * The flag is set server-side by:
 *  - admin user creation / CSV import / ERP import (first-login change), and
 *  - identity-api login, when the password is older than the expiry window
 *    configured in Admin → Security (see _password_expired()).
 *
 * Rendering a page rather than a modal is deliberate: no Portal content is
 * mounted behind it, so nothing leaks before the password is rotated. Sign Out
 * is the only other way out — /logout sits outside the auth guard.
 */
import { ShieldAlert } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { useBranding } from '@/hooks/useBranding'
import { globalSignOut } from '@/lib/signOut'
import { ChangePasswordForm } from '@/components/auth/ChangePasswordForm'

export default function ForcePasswordChangePage() {
  const user = useAuthStore((s) => s.user)
  const clearMustChangePassword = useAuthStore((s) => s.clearMustChangePassword)
  const { data: branding } = useBranding()

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F5F6FA] p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-xl">
        <div className="flex items-center gap-3 border-b border-neutral-100 px-6 py-5">
          {branding?.logo_data_url ? (
            <img src={branding.logo_data_url} alt={branding.name}
              className="h-10 w-10 rounded-xl bg-primary-50 object-contain p-1" />
          ) : (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-50">
              <ShieldAlert className="h-5 w-5 text-amber-600" />
            </div>
          )}
          <div>
            <h1 className="text-sm font-semibold text-neutral-900">Update your password to continue</h1>
            {user && <p className="text-xs text-neutral-500">{user.full_name} · {user.email}</p>}
          </div>
        </div>

        <div className="flex flex-col gap-4 px-6 py-5">
          <p className="text-xs leading-relaxed text-neutral-600">
            Your account requires a new password before you can use UniOps. This is asked on your
            first sign-in, after an administrator resets your password, and whenever your password
            passes the expiry period set by your administrator.
          </p>

          <ChangePasswordForm onSuccess={clearMustChangePassword} />

          <button
            onClick={globalSignOut}
            className="self-center text-xs text-neutral-400 hover:text-neutral-700 transition-colors"
          >
            Sign out instead
          </button>
        </div>
      </div>
    </div>
  )
}
