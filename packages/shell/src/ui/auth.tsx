/**
 * Sign-in frame: a brand panel (photo on the nav colour) beside a narrow form
 * column. The app supplies the words, the photo and the form; the look lives
 * here, so the sign-in screen re-skins with the rest of UniOps.
 *
 * Desktop: 900 : 540 split, proportional (user, 2026-11-18 — the earlier fixed
 * 440px form column left the brand panel ~2120px wide on a 2K screen). The form
 * column never goes below 400px; the form itself stays max 360px, centred.
 * Below lg: the brand panel becomes a short header above the form.
 */
import React from 'react'
import { cn } from '../lib/cn'
import type { Branding } from './chrome'

export interface AuthModule {
  key: string
  label: string
  summary: string
  /** lucide icon element */
  icon?: React.ReactNode
}

export interface AuthLayoutProps {
  brand: Branding
  /** Product line under the company name, e.g. "UniOps". */
  product?: string
  headline: React.ReactNode
  description?: React.ReactNode
  /** Background photo URL; shown at low opacity over the nav colour. */
  photoUrl?: string
  /** CSS object-position for the photo, to keep its subject in view. */
  photoPosition?: string
  /** Modules listed at the bottom of the brand panel (desktop only). */
  modules?: AuthModule[]
  footer?: React.ReactNode
  children: React.ReactNode
}

function Mark({ brand }: { brand: Branding }) {
  return brand.logoUrl ? (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white">
      <img src={brand.logoUrl} alt="" className="h-full w-full object-contain p-1" />
    </span>
  ) : (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-hero-fg/15 text-sm font-semibold">
      {brand.name.split(/\s+/).map((w) => w[0]).join('').toUpperCase().slice(0, 2)}
    </span>
  )
}

function BrandRow({ brand, product }: { brand: Branding; product?: string }) {
  return (
    <div className="flex items-center gap-3">
      <Mark brand={brand} />
      <div className="min-w-0">
        <p className="truncate text-[15px] font-semibold leading-5">{brand.name}</p>
        {/* Hide the product line when the company name already is the product. */}
        {product && product !== brand.name && <p className="truncate text-xs text-hero-fg/80">{product}</p>}
      </div>
    </div>
  )
}

function Photo({ url, position, mobile }: { url?: string; position?: string; mobile?: boolean }) {
  return (
    <>
      {url && (
        <img
          src={url}
          alt=""
          aria-hidden
          className="absolute inset-0 h-full w-full object-cover opacity-35"
          style={{ objectPosition: position ?? '38% 50%' }}
        />
      )}
      {/* Same-hue shading so white text stays readable over any photo. */}
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          background: mobile
            ? `linear-gradient(180deg, color-mix(in srgb, var(--color-primary-900) 40%, transparent) 0%, color-mix(in srgb, var(--color-primary-900) 75%, transparent) 100%)`
            : `linear-gradient(180deg, color-mix(in srgb, var(--color-primary-900) 55%, transparent) 0%, transparent 30%, transparent 55%, color-mix(in srgb, var(--color-primary-900) 85%, transparent) 100%)`,
        }}
      />
    </>
  )
}

export function AuthLayout({
  brand, product, headline, description, photoUrl, photoPosition, modules, footer, children,
}: AuthLayoutProps) {
  return (
    <div className="grid min-h-screen bg-surface lg:grid-cols-[minmax(0,900fr)_minmax(400px,540fr)]">
      {/* Desktop brand panel */}
      <section className="relative hidden overflow-hidden bg-hero text-hero-fg lg:block">
        <Photo url={photoUrl} position={photoPosition} />
        <div className="relative flex h-full min-h-screen flex-col justify-between px-16 py-12">
          <BrandRow brand={brand} product={product} />

          <div className="flex max-w-xl flex-col gap-3">
            <h2 className="text-[40px] font-semibold leading-[48px] tracking-[-0.01em]">{headline}</h2>
            {description && <p className="text-base text-hero-fg/85">{description}</p>}
          </div>

          <div className="flex flex-col gap-6">
            {modules && modules.length > 0 && (
              <div className="flex flex-col gap-3">
                <p className="text-xs font-medium text-hero-fg/80">Modules</p>
                <ul className="grid grid-cols-2 gap-x-8 gap-y-3.5">
                  {modules.map((m) => (
                    <li key={m.key} className="flex items-start gap-3">
                      {m.icon && (
                        <span className="mt-px flex shrink-0 text-hero-fg/80 [&>svg]:h-[18px] [&>svg]:w-[18px] [&>svg]:stroke-[1.5]">{m.icon}</span>
                      )}
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{m.label}</span>
                        <span className="block text-[13px] leading-[18px] text-hero-fg/80">{m.summary}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="text-xs text-hero-fg/80">Internal system · {brand.name}</p>
          </div>
        </div>
      </section>

      {/* Form column (and the short header on small screens) */}
      <div className="flex min-h-screen flex-col">
        <header className="relative overflow-hidden bg-hero text-hero-fg lg:hidden">
          <Photo url={photoUrl} position={photoPosition ?? '35% 50%'} mobile />
          <div className="relative flex flex-col gap-5 px-6 pb-7 pt-8">
            <BrandRow brand={brand} product={product} />
            <div className="flex flex-col gap-1.5">
              <p className="text-xl font-semibold leading-7">{headline}</p>
              {description && <p className="text-[13px] leading-[18px] text-hero-fg/85">{description}</p>}
            </div>
          </div>
        </header>

        <main className="flex flex-1 flex-col items-center justify-start px-6 py-8 lg:justify-center lg:px-10">
          <div className={cn('w-full max-w-[360px]')}>{children}</div>
        </main>

        {footer && <footer className="px-6 pb-8 text-center text-xs text-fg-muted">{footer}</footer>}
      </div>
    </div>
  )
}

/** Title block at the top of a sign-in form. */
export function AuthHeading({ title, description }: { title: React.ReactNode; description?: React.ReactNode }) {
  return (
    <div className="mb-7 flex flex-col gap-1">
      <h1 className="text-2xl font-semibold text-fg">{title}</h1>
      {description && <p className="text-sm text-fg-muted">{description}</p>}
    </div>
  )
}
