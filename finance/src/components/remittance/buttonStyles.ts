/**
 * Shared button classes for the remittance panel/dialog pair. Copied here
 * from JvDetailModal.tsx's local convention (`primaryBtn` / `secondaryBtn`)
 * rather than imported from there — JvDetailModal keeps its own local
 * copies and is not restructured by this file.
 */
export const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
export const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
