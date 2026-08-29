import { ReactNode, useId } from 'react'
import { Card } from './ui'
import { useCountUp } from '../hooks/gsapFx'

/* 本文件刻意独立于 ui.tsx：ui.tsx 被 App.tsx 引入（Spinner），处于首包，
   而 useCountUp 会拖入 gsap + ScrollTrigger，vite.config.ts 的 manualChunks 没有切分 gsap。
   放这里则只随 Dashboard / Talent 的懒加载分块走，不进首屏包。 */

/** 环形进度。**不再占用 KPI 卡的图标位**——40px 描边圆环在卡角上看着就是个 spinner，
    用户把「加权覆盖率」那张卡读成了转圈加载。现在只留给确实需要环形的独立场景。 */
export function Ring({ value, size = 40, stroke = 5 }: { value: number; size?: number; stroke?: number }) {
  // useId 产出形如 ":r0:"，冒号进 url(#…) 很脆，剥掉
  const uid = useId().replace(/:/g, '')
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const pct = Math.min(1, Math.max(0, value))
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0 -rotate-90" aria-hidden>
      <defs>
        {/* 与 bg-grad-accent 同源：直接引用 index.css 的 token 变量，
            改 token 时环形进度自动跟随，不会再出现"渐变各处不一致" */}
        <linearGradient id={`rg${uid}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="rgb(var(--brand-accent))" />
          <stop offset="100%" stopColor="rgb(var(--brand-accent-2))" />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--brand-ink) / 0.09)" strokeWidth={stroke} />
      {/* 扫过用 CSS transition：index.css 的 prefers-reduced-motion 已自动兜底 */}
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#rg${uid})`} strokeWidth={stroke}
        strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
        className="transition-[stroke-dashoffset] duration-700 ease-out" />
    </svg>
  )
}

/**
 * 全站统一的 KPI 卡片。Dashboard 与 Talent 共用一套实现。
 * value 传 number 时数字滚动入场；传 string（如 "12/18" 这类复合值）时直接渲染。
 * 四张卡的右上角**恒定是图标块**；ring 只在卡底追加一条细进度条。
 */
export function Kpi({ icon, label, value, unit, sub, tone = 'bg-grad-accent',
  ring, decimals = 0, delay = 0, hover = true }: {
  icon?: ReactNode
  label: string
  value: ReactNode
  unit?: string
  sub?: ReactNode
  tone?: string
  /** 传入 0~1 则在卡底附一条细进度条（不再替换图标块） */
  ring?: number
  decimals?: number
  delay?: number
  hover?: boolean
}) {
  const isNum = typeof value === 'number'
  // decimals 必须与下面渲染的字符串一致，否则动画最后一帧打印的数与 JSX 不同（78 vs 78.4）
  const numRef = useCountUp<HTMLSpanElement>(isNum ? (value as number) : 0, { decimals })
  const pct = ring == null ? null : Math.min(1, Math.max(0, ring))
  return (
    <Card delay={delay} hover={hover} className="p-5 relative overflow-hidden tg-topbar"
      decor="/kpi-texture.webp" decorClass="opacity-70">
      <div className="flex items-center justify-between gap-2">
        <div className="label">{label}</div>
        {/* 图标一律 currentColor，容器统一给 text-white：tone 是强调渐变/语义实色，白色前景才有对比 */}
        {icon && <div className={`w-9 h-9 rounded-xl grid place-items-center shrink-0 text-white ${tone}`}>{icon}</div>}
      </div>
      <div className="mt-3 flex items-baseline gap-1">
        {/* ref 必须挂在数字 span 上、不能挂 flex 容器：useCountUp 覆写 textContent，
            挂容器会把 unit 一起抹掉 */}
        <span ref={isNum ? numRef : undefined} className="text-3xl font-extrabold text-body-1 tabular-nums">
          {isNum ? (value as number).toFixed(decimals) : value}
        </span>
        {unit && <span className="text-sm font-semibold text-body-3">{unit}</span>}
      </div>
      {sub && <div className="text-xs text-body-2 mt-1 truncate">{sub}</div>}
      {pct != null && (
        <div className="mt-2.5 h-1 rounded-full bg-brand-ink/8 overflow-hidden" aria-hidden>
          <div className="h-full rounded-full bg-grad-fill transition-[width] duration-700 ease-out"
            style={{ width: `${Math.max(3, Math.round(pct * 100))}%` }} />
        </div>
      )}
    </Card>
  )
}
