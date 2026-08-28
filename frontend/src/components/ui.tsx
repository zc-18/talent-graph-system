import { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { WifiOff, RotateCcw, Inbox } from 'lucide-react'
import ConfidenceExplain from './ConfidenceExplain'
import type { ConfidenceFactors } from '../api'

export function PageHeader({ title, subtitle, icon, action }: {
  title: string; subtitle?: string; icon?: ReactNode; action?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div className="flex items-center gap-3">
        {icon && <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow">{icon}</div>}
        <div>
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
  // 三档刻度去绿：高=品牌蓝、中=中性墨蓝、低=琥珀警示，语义层级不变
  const color = value >= 0.75 ? 'text-accent bg-accent/10 border-accent/30'
    : value >= 0.5 ? 'text-body-2 bg-brand-ink/6 border-line-soft/14'
    : 'text-amber-700 bg-amber-50 border-amber-200'
  const pill = (
    <span className={`chip border ${color} ${factors ? 'underline decoration-dotted underline-offset-2' : ''}`}
      title={factors ? '点击查看置信度计算方式' : '能力置信度（交叉验证）'}>置信 {pct}%</span>
  )
  if (!factors) return pill
  return <ConfidenceExplain value={value} factors={factors}>{pill}</ConfidenceExplain>
}

export function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const map: Record<string, string> = {
    slate: 'bg-gradient-to-br from-surface-muted to-white text-body-2 border-line-soft/12',
    indigo: 'bg-gradient-to-br from-surface-muted to-white text-brand-ink border-brand-ink/15',
    cyan: 'bg-gradient-to-br from-brand-accent3 to-white text-accent-deep border-accent/22',
    // 紫仅供「新兴岗位」这类单点语义徽标使用，勿用作大面积背景
    violet: 'bg-accent-violet/8 text-accent-violet border-accent-violet/25',
    emerald: 'bg-accent/8 text-accent-deep border-accent/30',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    rose: 'bg-rose-50 text-rose-700 border-rose-200',
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
      <div className="w-12 h-12 rounded-2xl bg-rose-50 grid place-items-center">
        <WifiOff className="w-6 h-6 text-rose-400" />
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
