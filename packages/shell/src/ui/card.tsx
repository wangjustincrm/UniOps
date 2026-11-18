import { cn } from '../lib/cn'

interface CardProps {
  className?: string
  children: React.ReactNode
}

type CardTag = 'div' | 'section' | 'article' | 'aside' | 'li'

export interface CardRootProps extends React.HTMLAttributes<HTMLElement> {
  /** A box inside a card or panel: smaller radius, no shadow. */
  inset?: boolean
  /** Element to render (default div). */
  as?: CardTag
  /** React 19: ref is a plain prop on function components and reaches the element. */
  ref?: React.Ref<HTMLElement>
  children?: React.ReactNode
}

/**
 * Raised surface with a hairline — a page section. Padding comes from
 * CardContent / CardHeader, or the page's own p-* on className.
 * `inset` is the nested box (a group inside a card, a summary tile, a row
 * block): same surface and hairline, smaller radius, flat.
 */
/**
 * The card frame as classes, for an element that must stay what it is — a
 * router Link, a <form>, a <p>, a frame whose classes switch with state — so it
 * still re-skins with packages/shell. Prefer <Card> where the element is free.
 */
export function cardClass({ inset = false }: { inset?: boolean } = {}) {
  return cn(
    'border bg-surface',
    // border-line (not -subtle): white cards sit on a near-white canvas and in
    // white modals; the subtle hairline all but vanished there (2026-11-18).
    inset ? 'rounded-lg border-line' : 'rounded-xl border-line shadow-xs',
  )
}

export function Card({ className, children, inset, as = 'div', ...rest }: CardRootProps) {
  const Tag = as as React.ElementType
  return (
    <Tag
      className={cn(cardClass({ inset }), className)}
      {...rest}
    >
      {children}
    </Tag>
  )
}

export function CardHeader({ className, children, actions }: CardProps & { actions?: React.ReactNode }) {
  return (
    <div className={cn('flex items-center justify-between gap-3 border-b border-line-subtle px-5 py-4', className)}>
      <div className="min-w-0">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function CardContent({ className, children }: CardProps) {
  return <div className={cn('p-5', className)}>{children}</div>
}

export function CardFooter({ className, children }: CardProps) {
  return (
    <div className={cn('flex items-center justify-end gap-2 border-t border-line-subtle px-5 py-3', className)}>
      {children}
    </div>
  )
}

export function CardTitle({ className, children }: CardProps) {
  return <h3 className={cn('text-base font-semibold text-fg', className)}>{children}</h3>
}

export function CardDescription({ className, children }: CardProps) {
  return <p className={cn('mt-0.5 text-sm text-fg-muted', className)}>{children}</p>
}
