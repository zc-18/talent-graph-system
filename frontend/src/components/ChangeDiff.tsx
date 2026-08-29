import { ArrowRight } from 'lucide-react'
import { Badge } from './ui'

const IMPORTANCE: Record<string, string> = { required: '必备', bonus: '加分' }
const LEVEL: Record<string, string> = { familiar: '了解', proficient: '熟练', expert: '精通' }
const STATUS: Record<string, string> = { active: '确认能力项', candidate: '候选能力项', deprecated: '已淘汰' }

const fmtImportance = (v: any) => IMPORTANCE[v] || String(v)
const fmtLevel = (v: any) => LEVEL[v] || String(v)
const fmtWeight = (v: any) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : String(v))
const fmtStatus = (v: any) => STATUS[v] || String(v)
const fmtConf = (v: any) => (typeof v === 'number' ? v.toFixed(2) : String(v))

/** 从可能残缺的 old/new JSON 对象中提取一个简短摘要（新增/删除用） */
function summarize(v: any): string {
  if (!v || typeof v !== 'object') return v == null ? '' : String(v)
  const parts: string[] = []
  if (v.importance != null) parts.push(fmtImportance(v.importance))
  if (typeof v.weight === 'number') parts.push(`权重 ${fmtWeight(v.weight)}`)
  if (v.level_required != null) parts.push(fmtLevel(v.level_required))
  return parts.join(' · ')
}

function DiffArrow({ label, from, to }: { label: string; from: string; to: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-body-2 bg-white/70 border border-line-soft/8 rounded-full px-2 py-0.5">
      <span className="text-body-3">{label}</span>
      <span className="text-body-2">{from}</span>
      <ArrowRight className="w-3 h-3 text-warn" />
      <span className="font-semibold text-body-1">{to}</span>
    </span>
  )
}

/**
 * 能力项变更差异渲染器（岗位详情·演化历史 与 演化页共用）。
 * add → 绿色 + 新值摘要；delete → 红色 + 删除线旧值；
 * modify → 逐字段 旧 → 新 箭头（重要度/权重/掌握深度），对残缺 JSON 防御性读取。
 */
export default function ChangeDiff({ change, compact = false }: { change: any; compact?: boolean }) {
  const { change_type, old_value: ov, new_value: nv } = change || {}

  if (change_type === 'add') {
    const s = summarize(nv)
    return (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Badge tone="cyan">新增</Badge>
        {s && <span className="text-[11px] text-accent-deep bg-accent/8 border border-accent/25 rounded-full px-2 py-0.5">{s}</span>}
      </span>
    )
  }
  if (change_type === 'delete') {
    const s = summarize(ov)
    return (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Badge tone="rose">删除</Badge>
        {s && <span className="text-[11px] text-danger/80 line-through">{s}</span>}
      </span>
    )
  }
  if (change_type === 'modify') {
    const o = ov && typeof ov === 'object' ? ov : {}
    const n = nv && typeof nv === 'object' ? nv : {}
    const diffs: React.ReactNode[] = []
    if (o.importance != null && n.importance != null && o.importance !== n.importance)
      diffs.push(<DiffArrow key="imp" label="重要度" from={fmtImportance(o.importance)} to={fmtImportance(n.importance)} />)
    if (typeof o.weight === 'number' && typeof n.weight === 'number' && o.weight !== n.weight)
      diffs.push(<DiffArrow key="w" label="权重" from={fmtWeight(o.weight)} to={fmtWeight(n.weight)} />)
    if (o.level_required != null && n.level_required != null && o.level_required !== n.level_required)
      diffs.push(<DiffArrow key="lv" label="掌握深度" from={fmtLevel(o.level_required)} to={fmtLevel(n.level_required)} />)
    // 判据④「降级为候选能力项」：仅 status/confidence 变化，不落入上面三个字段，
    // 若不单独渲染会退化成一个没有任何说明的「修改」徽标。
    if (o.status !== n.status && (o.status != null || n.status != null))
      diffs.push(<DiffArrow key="st" label="状态" from={fmtStatus(o.status ?? 'active')} to={fmtStatus(n.status ?? 'active')} />)
    if (typeof o.confidence === 'number' && typeof n.confidence === 'number' && o.confidence !== n.confidence)
      diffs.push(<DiffArrow key="cf" label="置信度" from={fmtConf(o.confidence)} to={fmtConf(n.confidence)} />)
    return (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Badge tone="amber">修改</Badge>
        {diffs.length > 0 ? diffs : (
          !compact && <span className="text-[11px] text-body-3">{summarize(o)} → {summarize(n) || '（细节调整）'}</span>
        )}
      </span>
    )
  }
  return <Badge tone="slate">{change_type || '变更'}</Badge>
}
