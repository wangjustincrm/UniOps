import { clearMyDrafts } from './draftStore'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

export function signOut(): void {
  localStorage.removeItem('ehs-auth')
  // ★ And this person's drafts. They are keyed by user, so nobody else could
  // read them anyway — but a draft left on a shared phone after its author has
  // finished their shift is a copy of an injury report nobody is watching.
  clearMyDrafts()
  window.location.href = `${PORTAL_URL}/logout`
}
