/**
 * Shared button classes for the remittance panel/dialog pair. Copied here
 * from JvDetailModal.tsx's local convention (`primaryBtn` / `secondaryBtn`)
 * rather than imported from there — JvDetailModal keeps its own local
 * copies and is not restructured by this file.
 */
export const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50'
export const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-fg-secondary hover:bg-surface-subtle disabled:opacity-50'
