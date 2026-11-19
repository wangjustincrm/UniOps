// The shared UniOps components, re-exported so every component the module uses is
// reachable from '@/platform-ui'. Thin re-exports only — never a copy. A
// variant the module needs is a wrapper here, never a fork of @uniops/shell.
// This folder is the only place in the module (outside the app shell: main.tsx,
// components/layout/, app/) that imports @uniops/shell; ESLint enforces it.
import { createElement, type ComponentProps } from 'react'
import { cn } from '../lib/cn'
import { Badge as ShellBadge, Card, CardContent, CardHeader, CardTitle, FormField, Label, Pagination, Skeleton, SkeletonRow } from '@uniops/shell'

export {
  Button, Card, CardContent, CardHeader, CardTitle, FormField, Input, Label, Pagination, Skeleton, SkeletonRow,
} from '@uniops/shell'
export type { ButtonProps, InputProps } from '@uniops/shell'

export type BadgeProps = ComponentProps<typeof ShellBadge>

// The shell's tints with text dark enough to read on them (WCAG AA 4.5:1, step 6): its warning (2.9:1), success
// (3.3:1), danger (4.0:1) and neutral (4.0:1) text are too light on their own backgrounds. A wrapper, not a fork;
// the shell's own colours are for its owners to change (reported 2026-11-19).
const READABLE = { neutral: 'text-neutral-600', warning: 'text-warning-800', success: 'text-success-800', danger: 'text-danger-700', info: '', dark: '' } as const

export function Badge(props: BadgeProps) {
  return createElement(ShellBadge, { ...props, className: cn(READABLE[props.variant ?? 'neutral'], props.className) })
}
export type CardProps = ComponentProps<typeof Card>
export type CardHeaderProps = ComponentProps<typeof CardHeader>
export type CardContentProps = ComponentProps<typeof CardContent>
export type CardTitleProps = ComponentProps<typeof CardTitle>
export type FormFieldProps = ComponentProps<typeof FormField>
export type LabelProps = ComponentProps<typeof Label>
export type PaginationProps = ComponentProps<typeof Pagination>
export type SkeletonProps = ComponentProps<typeof Skeleton>
export type SkeletonRowProps = ComponentProps<typeof SkeletonRow>
