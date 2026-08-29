import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CalendarClock, RotateCcw, Target } from 'lucide-react'
import { api, errMsg, MatchHistoryItem } from '../api'
import { Badge, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import { useToast } from '../components/Toast'

/* 匹配分是 0–100 百分制，全站统一：services/matching.py:98 已经乘过 100，
   同文件 _grade() 按 85/70/55/40 分档，Match.tsx 也拿原值直接比 70/50。
   所以这里不做任何量纲换算——0–1 的小数只出现在种子脚本写的占位快照里，
   那是数据侧要修的东西，前端替它换算反而会让真实数据显示成 8950。 */

/* required_coverage = 1 时 overall = 100*(0.62*1 + …) 的下界就是 62 分。
   「一项必备能力都不缺」和「分数低于 62」因此不可能同时成立，同时出现
   只说明缺口明细没落进快照。见 backend/app/services/matching.py 的加权公式。 */
const FULL_COVERAGE_SCORE_FLOOR = 62

/* 「0 个关键缺口」原来同时表达两件事：真的一项必备能力都不缺，以及快照里压根
   没有缺口明细（结果未产出/未落库）。这里按证据强弱分三档，能证明才敢说全覆盖：
   ① 快照带 summary.required_matched/required_total —— 有计数，直接判定；
   ② 无计数但分数在满覆盖下界之上 —— 只说「未列出必备缺口」，不替数据下结论；
   ③ 其余（没有数组 / 没有级别 / 分数低于下界却列不出缺口）—— 判为明细缺失。
   字段以 missing_required 为准，top_gaps 是 me.py:30 对它的投影名。 */
function gapSummary(item: {
  top_gaps?: any[] | null; level?: string | null; overall_score?: number | null
  summary?: { required_total?: number; required_matched?: number } | null
}) {
  const gaps = Array.isArray(item.top_gaps) ? item.top_gaps : null
  if (gaps && gaps.length > 0) return { tone: 'amber', text: `${gaps.length} 个关键缺口` }

  const total = Number(item.summary?.required_total)
  const matched = Number(item.summary?.required_matched)
  if (Number.isFinite(total) && Number.isFinite(matched) && total > 0) {
    return matched >= total
      ? { tone: 'emerald', text: '必备能力全覆盖' }
      : { tone: 'slate', text: '缺口明细待补' }
  }

  const score = Number(item.overall_score)
  if (!gaps || !item.level || !Number.isFinite(score) || score < FULL_COVERAGE_SCORE_FLOOR)
    return { tone: 'slate', text: '缺口明细待补' }
  return { tone: 'cyan', text: '未列出必备缺口' }
}

export default function History() {
  const [items, setItems] = useState<MatchHistoryItem[]>([])
  const [selected, setSelected] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true); setError(false)
    api.matchHistory({ page: 1, size: 50 }).then(data => setItems(data.items || []))
      .catch(() => setError(true)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const open = async (item: MatchHistoryItem) => {
    try { setSelected(await api.matchHistoryDetail(item.id)) }
    catch (error) { toast('error', errMsg(error, '历史详情加载失败')) }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow"><CalendarClock className="w-5 h-5 text-white" /></div>
          <div><h1 className="text-2xl font-extrabold text-body-1">个人匹配历史</h1><p className="text-sm text-body-2">固定引用当时的岗位版本，可重新打开结果与学习路径</p></div>
        </div>
        <Link to="/match" className="btn-primary"><Target className="w-4 h-4" /> 新建匹配</Link>
      </div>

      {loading ? <Spinner /> : error ? <ErrorState text="匹配历史加载失败" onRetry={load} /> : items.length === 0 ? (
        <Card className="p-6"><EmptyState text="暂无匹配历史" hint="完成一次人岗匹配后，结果会保存在这里" /></Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] xl:grid-cols-[minmax(0,1fr)_420px] gap-4 lg:gap-5">
          <div className="space-y-3">
            {items.map(item => (
              <button key={item.id} onClick={() => open(item)} className="w-full text-left rounded-xl border border-line-soft/8 bg-white/75 p-4 hover:border-accent/40 hover:bg-white transition">
                <div className="flex items-start justify-between gap-3">
                  <div><div className="font-bold text-body-1">{item.job_name}</div><div className="text-xs text-body-3 mt-1">{new Date(item.created_at).toLocaleString()} · 岗位版本 v{item.job_version}</div></div>
                  <div className="flex shrink-0 items-center gap-2 sm:gap-3"><span className="text-xl font-extrabold text-body-1 tabular-nums sm:text-2xl">{Math.round(item.overall_score)}<span className="ml-0.5 text-xs font-bold text-body-3">分</span></span><ArrowRight className="w-4 h-4 text-body-3" /></div>
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-3"><Badge tone="cyan">{item.level || '匹配完成'}</Badge><Badge tone={gapSummary(item).tone}>{gapSummary(item).text}</Badge></div>
              </button>
            ))}
          </div>
          <Card className="p-5 lg:sticky lg:top-6 h-fit">
            {!selected ? <EmptyState text="选择一条历史查看快照" /> : (
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3"><div><div className="label">历史快照</div><h2 className="font-extrabold text-xl text-body-1 mt-1">{selected.job_name || selected.job?.name}</h2></div><Badge tone="cyan">v{selected.job_version || selected.job?.version}</Badge></div>
                <div className="rounded-xl bg-accent/6 p-4"><div className="text-xs text-body-2">当时综合匹配度</div><div className="mt-1 text-3xl font-extrabold text-body-1 tabular-nums">{Math.round(selected.overall_score ?? selected.result?.overall_score ?? 0)}<span className="ml-1 text-base font-bold text-body-3">分</span></div><div className="mt-0.5 text-[11px] text-body-3">百分制 · 取自当时的岗位版本快照</div></div>
                {(() => {
                  const gaps = selected.top_gaps ?? selected.result?.missing_required
                  const list = Array.isArray(gaps) ? gaps : null
                  const summary = gapSummary({
                    top_gaps: list ?? undefined,
                    level: selected.level ?? selected.result?.level,
                    overall_score: selected.overall_score ?? selected.result?.overall_score,
                    summary: selected.result?.summary,
                  })
                  return (
                    <div><div className="label mb-2">Top 关键缺口</div>{!list || list.length === 0
                      ? <div className={`rounded-xl px-3.5 py-3 text-sm leading-relaxed ${summary.tone === 'emerald' ? 'bg-accent/8 text-accent-deep' : summary.tone === 'cyan' ? 'bg-accent/8 text-accent-deep' : 'bg-surface-muted text-body-2'}`}>{summary.tone === 'emerald' ? '当时该岗位的必备能力已全部覆盖，没有关键缺口。' : summary.tone === 'cyan' ? '这条记录的快照没有列出必备缺口，但也没有留下覆盖计数，无法据此判定为零缺口。' : '这条记录的快照里没有缺口明细，无法判断是「一项不缺」还是「未记录」。'}</div>
                      : <div className="space-y-2">{list.slice(0, 10).map((gap: any, index: number) => <div key={gap.name || gap.skill || index} className="flex justify-between gap-3 text-sm border-b border-line-soft/6 pb-2"><span className="min-w-0 break-words text-body-1">{gap.name || gap.skill}</span><Badge tone={gap.importance === 'required' ? 'rose' : 'amber'}>{gap.priority || '待提升'}</Badge></div>)}</div>}</div>
                  )
                })()}
                <Link to="/match" state={{ jobId: selected.job_id || selected.job?.id }} className="btn-ghost w-full justify-center"><RotateCcw className="w-4 h-4" /> 按此岗位再次匹配</Link>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
