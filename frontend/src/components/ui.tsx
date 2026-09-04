import { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, WifiOff, RotateCcw, Inbox } from 'lucide-react'
import ConfidenceExplain from './ConfidenceExplain'
import type { ConfidenceFactors } from '../api'

/** 全站唯一的页头。四个页面此前各写各的标题块，图标块样式从此只有这一处。 */
export function PageHeader({ title, subtitle, icon, action }: {
  title: string; subtitle?: string; icon?: ReactNode; action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3">
        {/* text-white 挂在容器上：图标一律 currentColor，避免各页各写一个前景色 */}
        {icon && <div className="w-11 h-11 shrink-0 rounded-2xl bg-grad-accent grid place-items-center text-white shadow-glow">{icon}</div>}
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold tracking-tight text-body-1">{title}</h1>
          {subtitle && <p className="text-sm text-body-2 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

export function Card({ children, className = '', hover = false, delay = 0, decor, decorClass = '' }: {
  children: ReactNode; className?: string; hover?: boolean; delay?: number
  /** 卡片底层装饰图 URL（绝对定位铺满，内容自动抬到 z-10 之上） */
  decor?: string; decorClass?: string
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      whileHover={hover ? { y: -4 } : undefined}
      className={`glass ${hover ? 'glass-hover' : ''} ${decor ? 'relative overflow-hidden' : ''} ${className}`}>
      {decor && (
        <div aria-hidden className={`absolute inset-0 pointer-events-none bg-cover bg-center ${decorClass}`}
          style={{ backgroundImage: `url(${decor})` }} />
      )}
      {decor ? <div className="relative z-10">{children}</div> : children}
    </motion.div>
  )
}

export function Spinner({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-body-2 gap-3">
      <div className="w-9 h-9 rounded-full border-2 border-accent/15 border-t-accent animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

export function ConfidencePill({ value, factors }: { value: number; factors?: ConfidenceFactors | null }) {
  const pct = Math.round(value * 100)
  // 三档刻度走语义 token：高=强调蓝、中=中性、低=警示琥珀
  const color = value >= 0.75 ? 'text-accent-deep bg-accent/8 border-accent/25'
    : value >= 0.5 ? 'text-body-2 bg-brand-ink/6 border-line-soft/12'
    : 'text-warn bg-warn-weak border-warn/30'
  const pill = (
    <span className={`chip border ${color} ${factors ? 'underline decoration-dotted underline-offset-2' : ''}`}
      title={factors ? '点击查看置信度计算方式' : '能力置信度（交叉验证）'}>置信 {pct}%</span>
  )
  if (!factors) return pill
  return <ConfidenceExplain value={value} factors={factors}>{pill}</ConfidenceExplain>
}

export function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const map: Record<string, string> = {
    slate: 'bg-gradient-to-br from-surface-muted to-white text-body-2 border-line-soft/10',
    indigo: 'bg-gradient-to-br from-surface-muted to-white text-brand-ink border-brand-ink/12',
    cyan: 'bg-gradient-to-br from-brand-accent3 to-white text-accent-deep border-accent/22',
    // 紫仅供「新兴岗位」这类单点语义徽标使用，勿用作大面积背景
    violet: 'bg-accent-violet/10 text-accent-deep border-accent-violet/30',
    emerald: 'bg-success-weak text-success border-success/25',
    amber: 'bg-warn-weak text-warn border-warn/25',
    rose: 'bg-danger-weak text-danger border-danger/25',
  }
  return <span className={`chip border ${map[tone] || map.slate}`}>{children}</span>
}

/** 进度条：轨道 + 填充。零依赖，min 保证极小值仍有一丝可见宽度 */
export function Meter({ value, tone = 'bg-grad-fill', track = 'bg-brand-ink/8', h = 'h-1.5', min = 3 }: {
  value: number; tone?: string; track?: string; h?: string; min?: number
}) {
  return (
    <div className={`${h} rounded-full ${track} overflow-hidden`}>
      <div className={`h-full rounded-full ${tone}`}
        style={{ width: `${Math.max(min, Math.round(Math.min(1, Math.max(0, value)) * 100))}%` }} />
    </div>
  )
}

export function EmptyState({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-body-3 gap-2">
      <div className="w-12 h-12 rounded-2xl bg-brand-ink/6 grid place-items-center">
        <Inbox className="w-6 h-6 text-body-3" />
      </div>
      <div className="text-sm font-medium text-body-2">{text}</div>
      {hint && <div className="text-xs text-body-3">{hint}</div>}
    </div>
  )
}

export function ErrorState({ text = '数据加载失败，请检查网络后重试', onRetry }: { text?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <div className="w-12 h-12 rounded-2xl bg-danger-weak grid place-items-center">
        <WifiOff className="w-6 h-6 text-danger" />
      </div>
      <div className="text-sm text-body-2">{text}</div>
      {onRetry && (
        <button onClick={onRetry} className="btn-ghost text-sm">
          <RotateCcw className="w-4 h-4" /> 重试
        </button>
      )}
    </div>
  )
}

function paginationItems(page: number, totalPages: number): Array<number | string> {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1)
  if (page <= 4) return [1, 2, 3, 4, 5, 'right-gap', totalPages]
  if (page >= totalPages - 3) return [1, 'left-gap', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]
  return [1, 'left-gap', page - 1, page, page + 1, 'right-gap', totalPages]
}

export function Pagination({ page, pageSize, total, onChange, disabled = false, label = '分页导航' }: {
  page: number; pageSize: number; total: number; onChange: (page: number) => void
  disabled?: boolean; label?: string
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null
  const go = (next: number) => {
    if (!disabled && next >= 1 && next <= totalPages && next !== page) onChange(next)
  }
  return (
    <nav aria-label={label}
      className="flex min-h-12 flex-wrap items-center justify-between gap-2 rounded-xl border border-line-soft/8 bg-white/75 px-3 py-2 shadow-[0_6px_18px_-16px_rgb(var(--brand-ink)/0.30)]">
      <span className="text-xs tabular-nums text-body-2">第 <b className="text-body-1">{page}</b> / {totalPages} 页 · 共 {total} 条</span>
      <div className="flex items-center gap-1.5">
        <button type="button" onClick={() => go(page - 1)} disabled={disabled || page <= 1}
          title="上一页" aria-label="上一页"
          className="grid h-8 w-8 place-items-center rounded-lg border border-line-soft/10 bg-white text-body-2 transition hover:border-accent/35 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-40">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="hidden items-center gap-1 sm:flex">
          {paginationItems(page, totalPages).map(item => typeof item === 'number' ? (
            <button type="button" key={item} onClick={() => go(item)} disabled={disabled}
              aria-label={`第 ${item} 页`} aria-current={item === page ? 'page' : undefined}
              className={`grid h-8 min-w-8 place-items-center rounded-lg px-2 text-xs font-semibold tabular-nums transition ${
                item === page
                  ? 'bg-grad-accent text-white shadow-[0_6px_16px_-8px_rgb(var(--brand-accent)/0.55)]'
                  : 'border border-line-soft/8 bg-white text-body-2 hover:border-accent/35 hover:bg-surface-muted'
              }`}>
              {item}
            </button>
          ) : <span key={item} className="grid h-8 w-6 place-items-center text-xs text-body-3">…</span>)}
        </div>
        <span className="min-w-14 text-center text-xs font-semibold tabular-nums text-body-2 sm:hidden">{page} / {totalPages}</span>
        <button type="button" onClick={() => go(page + 1)} disabled={disabled || page >= totalPages}
          title="下一页" aria-label="下一页"
          className="grid h-8 w-8 place-items-center rounded-lg border border-line-soft/10 bg-white text-body-2 transition hover:border-accent/35 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-40">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </nav>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl bg-brand-ink/8 ${className}`} aria-hidden="true" />
}

// 页面级骨架：KPI 行 + 两块内容区，替代整页 Spinner
export function PageSkeleton() {
  return (
    <div className="space-y-6" aria-label="加载中" role="status">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map(i => <Skeleton key={i} className="h-28 glass !bg-white/50" />)}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Skeleton className="h-64 glass !bg-white/50" />
        <Skeleton className="h-64 glass !bg-white/50" />
      </div>
    </div>
  )
}
