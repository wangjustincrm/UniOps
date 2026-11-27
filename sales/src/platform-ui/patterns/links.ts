// A record's address as a link: copied whole, or opened in the browser's own new tab (RefLink, the action bar).

/** The record's address as a whole URL (for Copy link). */
export const absoluteHref = (href: string) => new URL(href, window.location.origin).toString()

export async function copyLink(href: string): Promise<void> {
  await navigator.clipboard.writeText(absoluteHref(href))
}

/** The browser's own new tab for an address (Open in a new browser tab). */
export function openInBrowserTab(href: string): void {
  window.open(absoluteHref(href), '_blank', 'noopener')
}
