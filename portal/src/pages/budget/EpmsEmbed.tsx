/**
 * EpmsEmbed — renders a Portal page that wraps an EPMS route in an iframe.
 *
 * Used by Finance/Budget routes so that users can navigate Budget Dashboard,
 * Plans and Account Catalog without leaving the UniOps Portal sidebar.
 * EPMS's AppLayout detects `window.self !== window.top` at runtime and hides
 * its own Sidebar/Header, leaving only the page content inside the frame.
 *
 * Session handoff is via `#__session=<base64json>` on the iframe `src`, the
 * same mechanism used for full-page jumps to EPMS.
 */
import { useMemo } from 'react'
import { Navigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { EPMS_URL, encodeSession } from '@/lib/api'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

interface EpmsEmbedProps {
  /** EPMS-relative path to load in the iframe (e.g. "/budget", "/budget/plans"). */
  epmsPath: string
  /** Title shown above the iframe — also used as document title. */
  title: string
  /** Optional subtitle. */
  subtitle?: string
  /** Active sidebar item key — must match a NAV item href. */
  activeKey: string
}

function buildSrc(epmsPath: string, session: string): string {
  // The hash session handoff is consumed by EPMS on first load; the iframe
  // is then free to navigate internally. We don't need to re-stamp the hash
  // on every navigation because EPMS persists the session in localStorage.
  return session
    ? `${EPMS_URL}${epmsPath}#__session=${session}`
    : `${EPMS_URL}${epmsPath}`
}

export default function EpmsEmbed({ epmsPath, title, subtitle, activeKey }: EpmsEmbedProps) {
  const auth = useAuthStore()
  if (!auth.isAuthenticated) return <Navigate to="/login" replace />

  const session = auth.token && auth.user
    ? encodeSession(auth.token, auth.refreshToken ?? '', auth.user)
    : ''

  // src is memoized so React doesn't reload the iframe on every render.
  const src = useMemo(() => buildSrc(epmsPath, session), [epmsPath, session])

  const openInEpmsHref = `${EPMS_URL}${epmsPath}${session ? `#__session=${session}` : ''}`

  return (
    <PortalChromeLayout
      activeKey={activeKey}
      title={title}
      subtitle={subtitle}
      scrollableBody={false}
      flushBody
      headerActions={
        <a
          href={openInEpmsHref}
          target="_blank" rel="noopener noreferrer"
          className="hidden md:inline-flex items-center gap-1.5 rounded-md border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-600 hover:bg-neutral-50"
          title="Open this page in a new tab (with EPMS chrome)"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Open in EPMS
        </a>
      }
    >
      <iframe
        title={title}
        src={src}
        className="block w-full h-full border-0 bg-white"
      />
    </PortalChromeLayout>
  )
}
