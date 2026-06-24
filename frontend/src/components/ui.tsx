import { ReactNode } from 'react'
import { motion } from 'framer-motion'

export function PageHeader({ title, subtitle, icon, action }: {
  title: string; subtitle?: string; icon?: ReactNode; action?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div className="flex items-center gap-3">
        {icon && <div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow">{icon}</div>}
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">{title}</h1>
          {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

export function Card({ children, className = '', hover = false, delay = 0 }: {
  children: ReactNode; className?: string; hover?: boolean; delay?: number
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      whileHover={hover ? { y: -4 } : undefined}
      className={`glass ${hover ? 'glass-hover' : ''} ${className}`}>
      {children}
    </motion.div>
  )
}

export function Spinner({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-slate-500 gap-3">
      <div className="w-9 h-9 rounded-full border-2 border-sky-100 border-t-accent animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

export function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = value >= 0.75 ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
    : value >= 0.5 ? 'text-sky-700 bg-sky-50 border-sky-200'
    : 'text-amber-700 bg-amber-50 border-amber-200'
  return <span className={`chip border ${color}`} title="能力置信度（交叉验证）">置信 {pct}%</span>
}

export function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const map: Record<string, string> = {
    slate: 'bg-slate-100 text-slate-600 border-slate-200',
    indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200',
    cyan: 'bg-cyan-50 text-cyan-700 border-cyan-200',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    rose: 'bg-rose-50 text-rose-700 border-rose-200',
  }
  return <span className={`chip border ${map[tone] || map.slate}`}>{children}</span>
}

export function EmptyState({ text }: { text: string }) {
  return <div className="text-center py-16 text-slate-400 text-sm">{text}</div>
}
