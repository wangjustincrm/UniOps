import * as Lucide from 'lucide-react'
import type { ComponentType } from 'react'

type IconProps = { className?: string }

export function resolveIcon(name?: string): ComponentType<IconProps> | null {
  if (!name) return null
  const lib = Lucide as unknown as Record<string, ComponentType<IconProps>>
  return lib[name] ?? null
}
