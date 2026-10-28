import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// @uniops/shell keeps its own cn internal; this is the same two-liner.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
