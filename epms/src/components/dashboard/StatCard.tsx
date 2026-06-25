import { cn } from '@/lib/utils'

interface StatCardProps {
  title: string
  value: string | number
  subtitle?: string
  trend?: 'up' | 'down' | 'neutral'
  alert?: boolean
  className?: string
}

export function StatCard({ title, value, subtitle, alert, className }: StatCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg border bg-white px-5 py-4',
        alert ? 'border-danger-200 bg-danger-50' : 'border-neutral-200',
        className
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-neutral-500">{title}</p>
      <p
        className={cn(
          'mt-1 text-2xl font-bold',
          alert ? 'text-danger-600' : 'text-neutral-900'
        )}
      >
        {value}
      </p>
      {subtitle && (
        <p className="mt-0.5 text-xs text-neutral-400">{subtitle}</p>
      )}
    </div>
  )
}
