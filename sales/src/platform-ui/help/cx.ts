/** Class names joined, falsy ones dropped (the module's own: it imports nothing from the library around it). */
export const cx = (...parts: (string | false | null | undefined)[]): string => parts.filter(Boolean).join(' ')
