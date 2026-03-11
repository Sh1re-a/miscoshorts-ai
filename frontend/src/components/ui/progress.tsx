import { cn } from '../../lib/utils'

export function Progress({ value = 0, className }: { value?: number; className?: string }) {
  return (
    <div className={cn('relative h-3 w-full overflow-hidden rounded-full bg-white/10', className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-[#f59e0b] via-[#f4cf8e] to-[#2dd4bf] transition-all duration-500"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  )
}