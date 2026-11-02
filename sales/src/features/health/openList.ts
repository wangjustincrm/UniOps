import { requestView } from '@/platform-ui'
import type { ListLink } from './healthApi'

/** Opens the list behind a card or check: its own filters, under the card's
 *  name, in the page's (possibly already open) tab. Returns the page to go to. */
export function openList(link: ListLink, name: string): string {
  if (link.resource && link.filters?.length) requestView(link.resource, { name, state: { filters: link.filters } })
  return link.page
}
