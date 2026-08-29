import { Clock3, Minus, TrendingDown, TrendingUp } from 'lucide-react'
import type { ConfidenceSnapshot } from '../api'
import { formatDataTime } from '../presentation'

function snapshotScore(item: ConfidenceSnapshot): number | null {
  const value = item.confidence ?? item.score_after ?? item.score
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function snapshotTime(item: ConfidenceSnapshot): string | null {
  return item.computed_at || item.as_of || item.created_at || null
}

/* 变化量小于半个百分点的千分之一时按「持平」处理。
   没有这道 epsilon，-0.0002 这种数会走 delta > 0 为假的分支（不加正号）、
   又过不了 === 0 的判定，最后被 toFixed(1) 抹成「较上次 -0.0%」，还配一个红色下降箭头。 */
const DELTA_EPSILON = 0.0005

export function ConfidenceMeta({ asOf, delta, compact = false }: {
  asOf?: string | null
  delta?: number | null
  compact?: boolean
}) {
  const raw = asOf ? delta : null
  const flat = raw == null || Math.abs(raw) < DELTA_EPSILON
  const TrendIcon = flat ? Minus : raw! > 0 ? TrendingUp : TrendingDown
  const tone = flat ? 'text-body-3' : raw! > 0 ? 'text-accent-deep' : 'text-danger'
  const deltaLabel = raw == null ? '暂无历史快照'
    : flat ? '较上次持平'
    : `较上次 ${raw > 0 ? '+' : ''}${(raw * 100).toFixed(1)}%`

  return (
    <div className={`flex min-w-0 flex-wrap items-center ${compact ? 'gap-x-2 gap-y-1 text-[10px]' : 'gap-x-3 gap-y-1.5 text-xs'}`}>
      <span className="inline-flex min-w-0 items-center gap-1 text-body-3" title={asOf || undefined}>
        <Clock3 className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{formatDataTime(asOf)}</span>
      </span>
      <span className={`inline-flex items-center gap-1 font-semibold ${tone}`}>
        <TrendIcon className="h-3.5 w-3.5 shrink-0" />{deltaLabel}
      </span>
    </div>
  )
}

export function ConfidenceTrend({ items }: { items: ConfidenceSnapshot[] }) {
  const points = items
    .map(item => ({ score: snapshotScore(item), time: snapshotTime(item) }))
    .filter((item): item is { score: number; time: string | null } => item.score != null)
    .sort((a, b) => (a.time ? new Date(a.time).getTime() : 0) - (b.time ? new Date(b.time).getTime() : 0))
    .slice(-12)

  if (points.length < 2) {
    return <div className="flex h-20 items-center justify-center border-t border-line-soft/8 px-4 text-xs text-body-3 md:border-l md:border-t-0">历史样本不足</div>
  }

  const width = 260
  const height = 64
  const pad = 5
  const min = Math.min(...points.map(point => point.score))
  const max = Math.max(...points.map(point => point.score))
  const range = Math.max(0.02, max - min)
  const coords = points.map((point, index) => {
    const x = pad + index * ((width - pad * 2) / Math.max(1, points.length - 1))
    const y = height - pad - ((point.score - min) / range) * (height - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const first = points[0]
  const last = points[points.length - 1]

  return (
    <figure className="min-w-0 border-t border-line-soft/8 pt-4 md:border-l md:border-t-0 md:pl-4 md:pt-0" aria-label={`置信度趋势，共 ${points.length} 个历史快照`}>
      <div className="mb-1 flex items-center justify-between gap-3 text-[11px] text-body-3">
        <span>历史趋势 · {points.length} 个快照</span>
        <span className="font-semibold tabular-nums text-body-2">{Math.round(first.score * 100)}% → {Math.round(last.score * 100)}%</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-14 w-full overflow-visible" role="img">
        <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="rgb(var(--line-soft) / 0.14)" strokeWidth="1" />
        <polyline points={coords} fill="none" stroke="rgb(var(--brand-accent))" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {points.map((point, index) => {
          const [x, y] = coords.split(' ')[index].split(',')
          return <circle key={`${point.time}-${index}`} cx={x} cy={y} r={index === points.length - 1 ? 3.5 : 2} fill={index === points.length - 1 ? 'rgb(var(--brand-accent-deep))' : 'rgb(var(--brand-accent-2))'}><title>{`${point.time ? formatDataTime(point.time) : '时间未记录'} · ${Math.round(point.score * 100)}%`}</title></circle>
        })}
      </svg>
    </figure>
  )
}
