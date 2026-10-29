// The shared UniOps components, re-exported so every component the module uses is
// reachable from '@/platform-ui'. Thin re-exports only — never a copy. A
// variant the module needs is a wrapper here, never a fork of @uniops/shell.
// This folder is the only place in the module (outside the app shell: main.tsx,
// components/layout/, app/) that imports @uniops/shell; ESLint enforces it.
import type { ComponentProps } from 'react'
import { Badge, Card, CardContent, CardHeader, CardTitle, FormField, Label, Pagination, Skeleton, SkeletonRow } from '@uniops/shell'

export {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, FormField, Input, Label, Pagination, Skeleton, SkeletonRow,
} from '@uniops/shell'
export type { ButtonProps, InputProps } from '@uniops/shell'

export type BadgeProps = ComponentProps<typeof Badge>
export type CardProps = ComponentProps<typeof Card>
export type CardHeaderProps = ComponentProps<typeof CardHeader>
export type CardContentProps = ComponentProps<typeof CardContent>
export type CardTitleProps = ComponentProps<typeof CardTitle>
export type FormFieldProps = ComponentProps<typeof FormField>
export type LabelProps = ComponentProps<typeof Label>
export type PaginationProps = ComponentProps<typeof Pagination>
export type SkeletonProps = ComponentProps<typeof Skeleton>
export type SkeletonRowProps = ComponentProps<typeof SkeletonRow>
