/** Admin Panel shell (PRD §3.2 — `system_admin` only).
 *
 * Tab navigation across the four admin sub-pages:
 *   - Zones (access zones, their GMP grade and zone managers)
 *   - Notification Contacts (W11)
 *   - Health Questions (W11)
 *   - Badge Templates (W5 backend stub, this page is the first real UI)
 *
 * Role guard lives here so the sub-pages don't each repeat it. Server-side
 * each /admin/* endpoint also enforces system_admin (defense in depth).
 */
import { useState } from 'react'
import {
  AlertCircle, Bell, ClipboardList, Image, Mail, MapPin,
} from 'lucide-react'
import ZonesPage from '@/pages/admin/ZonesPage'
import NotificationContactsPage from '@/pages/admin/NotificationContactsPage'
import HealthQuestionsPage from '@/pages/admin/HealthQuestionsPage'
import BadgeTemplatesPage from '@/pages/admin/BadgeTemplatesPage'
import EmailSettingsPage from '@/pages/admin/EmailSettingsPage'
import { Tabs } from '@uniops/shell'

type Tab = 'zones' | 'notifications' | 'email' | 'health' | 'badges'

const TABS: { key: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'zones',         label: 'Zones',                 icon: MapPin },
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
  const [tab, setTab] = useState<Tab>('zones')
  const role = getRole()

  if (role && role !== 'system_admin') {
    return (
      <div className="max-w-md rounded-lg border border-danger-line bg-danger-soft p-5 text-sm text-danger-fg">
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
      <h1 className="text-2xl font-semibold text-fg">VMS Admin</h1>
      <p className="mt-1 text-sm text-fg-muted">
        Zones, notification, email, questionnaire, and badge template configuration.
      </p>

      {/* Tabs */}
      <Tabs<Tab>
        className="mt-6"
        items={TABS.map(({ key, label, icon: Icon }) => ({
          key, label: <span className="inline-flex items-center gap-1.5"><Icon className="h-4 w-4" />{label}</span>,
        }))}
        value={tab}
        onChange={setTab}
      />

      {/* Body */}
      <div className="mt-6">
        {tab === 'zones'         && <ZonesPage />}
        {tab === 'notifications' && <NotificationContactsPage />}
        {tab === 'email'         && <EmailSettingsPage />}
        {tab === 'health'        && <HealthQuestionsPage />}
        {tab === 'badges'        && <BadgeTemplatesPage />}
      </div>
    </div>
  )
}
