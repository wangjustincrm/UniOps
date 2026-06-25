/** Admin Panel shell (PRD §3.2 — `system_admin` only).
 *
 * Tab navigation across the four admin sub-pages:
 *   - Quality Managers (W10 backend, this page is W11 UI)
 *   - Notification Contacts (W11)
 *   - Health Questions (W11)
 *   - Badge Templates (W5 backend stub, this page is the first real UI)
 *
 * Role guard lives here so the sub-pages don't each repeat it. Server-side
 * each /admin/* endpoint also enforces system_admin (defense in depth).
 */
import { useState } from 'react'
import {
  AlertCircle, ShieldCheck, Bell, ClipboardList, Image, Mail,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import QualityManagerRosterPage from '@/pages/admin/QualityManagerRosterPage'
import NotificationContactsPage from '@/pages/admin/NotificationContactsPage'
import HealthQuestionsPage from '@/pages/admin/HealthQuestionsPage'
import BadgeTemplatesPage from '@/pages/admin/BadgeTemplatesPage'
import EmailSettingsPage from '@/pages/admin/EmailSettingsPage'

type Tab = 'qm' | 'notifications' | 'email' | 'health' | 'badges'

const TABS: { key: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'qm',            label: 'Quality Managers',      icon: ShieldCheck },
  { key: 'notifications', label: 'Notification Contacts', icon: Bell },
  { key: 'email',         label: 'Email Settings',        icon: Mail },
  { key: 'health',        label: 'Health Questions',      icon: ClipboardList },
  { key: 'badges',        label: 'Badge',                 icon: Image },
]

function getRole(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.user?.role) return state.user.role
    }
    return null
  } catch { return null }
}

export default function AdminPanel() {
  const [tab, setTab] = useState<Tab>('qm')
  const role = getRole()

  if (role && role !== 'system_admin') {
    return (
      <div className="max-w-md rounded-lg border border-red-200 bg-danger-50 p-5 text-sm text-danger-600">
        <div className="flex items-center gap-2 font-semibold">
          <AlertCircle className="h-4 w-4" />
          Forbidden
        </div>
        <p className="mt-1.5 text-xs">
          The VMS Admin Panel is restricted to system administrators.
        </p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-neutral-900">VMS Admin</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Roster, notification, email, questionnaire, and badge template configuration.
      </p>

      {/* Tabs */}
      <div className="mt-6 border-b border-neutral-200">
        <nav className="-mb-px flex flex-wrap gap-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                'inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                tab === key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-neutral-500 hover:border-neutral-200 hover:text-neutral-700',
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* Body */}
      <div className="mt-6">
        {tab === 'qm'            && <QualityManagerRosterPage />}
        {tab === 'notifications' && <NotificationContactsPage />}
        {tab === 'email'         && <EmailSettingsPage />}
        {tab === 'health'        && <HealthQuestionsPage />}
        {tab === 'badges'        && <BadgeTemplatesPage />}
      </div>
    </div>
  )
}
