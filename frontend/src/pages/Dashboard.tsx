import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, GitBranch } from 'lucide-react'
import {
  IJob, ISkill, IJd, IDuplicate, IConfidence, IEvolution, IEmployer, IEvidence, IGauge,
} from '../components/icons'
import { api, Stats, JobListItem, PipelineStats, CATEGORY_COLORS } from '../api'
import { Card, ConfidencePill, Badge, PageSkeleton, ErrorState, PageHeader } from '../components/ui'
import { Kpi } from '../components/Kpi'
import { useReveal } from '../hooks/gsapFx'
import { ConfidenceMeta } from '../components/ConfidenceMeta'

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [pipe, setPipe] = useState<PipelineStats | null>(null)
  const [error, setError] = useState(false)
  const nav = useNavigate()

  const load = () => {
    setError(false)
    api.stats().then(setStats).catch(() => setError(true))
    api.jobs({ size: 8 }).then(d => setJobs(d.items)).catch(() => {})
    api.pipelineStats().then(setPipe).catch(() => setPipe(null))
  }
  useEffect(load, [])
  const revealRef = useReveal('[data-reveal]', { scroll: true, deps: [stats] })

  if (error) return <ErrorState text="驾驶舱数据加载失败" onRetry={load} />
  if (!stats) return <PageSkeleton />

  const catData = Object.entries(stats.categories).map(([name, value]) => ({
    name, value, itemStyle: { color: CATEGORY_COLORS[name] || '#64748B' },
  }))
  const donut = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: '#64748B', fontSize: 11 }, itemWidth: 10, itemHeight: 10, itemGap: 10, pageIconSize: 10 },
    series: [{
      type: 'pie', radius: ['52%', '78%'], center: ['50%', '44%'], avoidLabelOverlap: false,
      itemStyle: { borderColor: '#ffffff', borderWidth: 3 },
      label: { show: true, position: 'center', formatter: () => `${stats.total_jobs}\n岗位`,
        color: '#0F172A', fontSize: 22, fontWeight: 700, lineHeight: 22 },
      labelLine: { show: false }, data: catData,
    }],
  }
  const factors = stats.factor_averages || { support: 0, diversity: 0, freshness: 0, authority: 0, external: 0 }
  const factorRows = [
    ['support', '支持率', factors.support, 'bg-accent'],
    ['diversity', '来源多样性', factors.diversity, 'bg-brand-accent2'],
    ['freshness', '时效性', factors.freshness, 'bg-accent-deep'],
    ['authority', '来源权威度', factors.authority, 'bg-accent-violet'],
    ['external', '外部验证', factors.external, 'bg-warn'],
  ] as const
  const confidenceBands = [
    ['90_and_above', '≥ 90%', 'bg-accent-deep'],
    ['80_to_90', '80–90%', 'bg-accent'],
    ['60_to_80', '60–80%', 'bg-warn'],
    ['below_60', '< 60%', 'bg-danger'],
  ] as const

  return (
    <div ref={revealRef} className="space-y-5">
      <div className="border-b border-line-soft/8 pb-5">
        <PageHeader icon={<IGauge className="w-6 h-6" />} title="数据驾驶舱"
          subtitle="岗位证据、置信度与能力契约的当前运行状态"
          action={<ConfidenceMeta asOf={stats.confidence_as_of} delta={stats.avg_confidence_delta} />} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi delay={0} label="岗位总数" value={stats.total_jobs} sub={`新兴岗位 ${stats.new_jobs} 个`}
          icon={<IJob className="w-5 h-5" />} tone="bg-grad-accent" />
        <Kpi delay={0.05} label="技能点" value={stats.total_skills} sub="技能级粒度"
          icon={<ISkill className="w-5 h-5" />} tone="bg-grad-violet" />
        <Kpi delay={0.1} label="已处理 JD" value={stats.total_jds} sub={`覆盖 ${Object.keys(stats.categories).length} 大技术栈`}
          icon={<IJd className="w-5 h-5" />} tone="bg-grad-accent" />
        <Kpi delay={0.15} label="抄袭/重复拦截" value={stats.duplicate_jds} sub="交叉验证去噪"
          icon={<IDuplicate className="w-5 h-5" />} tone="bg-danger" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-[minmax(280px,0.82fr)_minmax(360px,1.18fr)_minmax(320px,1fr)]">
        <Card delay={0.1} className="p-4">
          <div className="mb-1 font-semibold text-body-1">技术栈分布</div>
          <div className="text-xs text-body-3">已发布岗位按技术领域聚合</div>
          <ReactECharts option={donut} style={{ height: 238 }} />
        </Card>
        <Card delay={0.15} className="p-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 font-semibold text-body-1"><IConfidence className="h-4 w-4 text-accent-deep" />置信度与五因子覆盖</div>
              <div className="mt-1 text-xs text-body-3">全库平均置信度</div>
            </div>
            <div className="text-3xl font-extrabold tabular-nums text-accent-deep">{(stats.avg_confidence * 100).toFixed(1)}%</div>
          </div>
          <div className="mt-4 grid grid-cols-4 gap-1.5">
            {confidenceBands.map(([key, label, tone]) => {
              const band = stats.confidence_distribution?.[key] || { count: 0, ratio: 0 }
              return <div key={key} className="rounded-lg border border-line-soft/6 bg-surface-muted px-2 py-2 text-center"><div className="text-base font-bold tabular-nums text-body-1">{band.count}</div><div className="text-[10px] text-body-3">{label}</div><div className="mt-1 h-1 overflow-hidden rounded-full bg-brand-ink/12"><div className={`h-full ${tone}`} style={{ width: `${Math.round(band.ratio * 100)}%` }} /></div></div>
            })}
          </div>
          <div className="mt-4 space-y-2">
            {factorRows.map(([key, label, value, tone]) => <div key={key} className="grid grid-cols-[58px_minmax(0,1fr)_34px] items-center gap-1.5 sm:grid-cols-[76px_minmax(0,1fr)_38px] sm:gap-2"><span className="truncate text-[10px] text-body-2 sm:text-[11px]" title={label}>{label}</span><div className="h-1.5 overflow-hidden rounded-full bg-brand-ink/8"><div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.round(value * 100)}%` }} /></div><b className="text-right text-[11px] tabular-nums text-body-1">{Math.round(value * 100)}%</b></div>)}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2 border-t border-line-soft/6 pt-3">
            <div className="rounded-lg bg-accent-violet/10 px-3 py-2"><div className="flex items-center gap-1.5 text-[10px] font-semibold text-accent-deep"><IEmployer className="h-3.5 w-3.5" />已识别雇主</div><div className="mt-1 text-lg font-bold tabular-nums text-body-1">{Math.round((stats.identified_employer_coverage || 0) * 100)}%</div></div>
            <div className="rounded-lg bg-accent/8 px-3 py-2"><div className="flex items-center gap-1.5 text-[10px] font-semibold text-accent-deep"><IEvidence className="h-3.5 w-3.5" />有效证据 URL</div><div className="mt-1 text-lg font-bold tabular-nums text-body-1">{Math.round((stats.valid_evidence_url_ratio || 0) * 100)}%</div></div>
          </div>
        </Card>
        <Card delay={0.2} className="p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold text-body-1">岗位能力榜</div>
            <button onClick={() => nav('/jobs')} className="text-xs text-accent hover:underline flex items-center gap-1">
              全部 <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <div className="max-h-[260px] space-y-1.5 overflow-auto pr-1 sm:max-h-[328px]">
            {jobs.map(j => (
              <button key={j.id} onClick={() => nav(`/jobs/${j.id}`)}
                className="flex w-full items-center justify-between rounded-lg border border-transparent bg-accent/6 px-3 py-2 text-left transition hover:border-accent/25 hover:bg-accent/12">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-body-1 truncate flex items-center gap-2">
                    {j.name}
                    {j.is_new && <Badge tone="amber">新兴</Badge>}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-body-3"><span>{j.category}</span><span>·</span><span>{j.required_count} 个契约能力簇</span><span>·</span><span>{j.employer_count || 0} 个雇主</span>{j.contract_status === 'evidence_insufficient' && <Badge tone="amber">证据待补</Badge>}</div>
                </div>
                <ConfidencePill value={j.confidence} />
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card delay={0.25} className="p-6">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
          <div className="flex items-center gap-2 font-semibold text-body-1">
            <IEvolution className="w-4 h-4 text-accent-deep" /> 全流程闭环
          </div>
          {pipe && (
            <div className="flex items-center gap-1.5 text-xs text-body-2">
              <GitBranch className="w-3.5 h-3.5 text-accent-deep" />
              已完成演化运行 <span className="font-bold text-accent-deep tabular-nums">{pipe.loop.evolution_runs}</span> 轮
            </div>
          )}
        </div>
        {pipe ? (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <section aria-labelledby="jd-flow-title">
                <div id="jd-flow-title" className="text-xs font-bold text-body-1 mb-2">JD 数据流 <span className="font-normal text-body-3">· 计量单位：条 JD</span></div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    ['采集', pipe.funnel.collected, '原始 JD'],
                    ['去重后', pipe.funnel.after_dedup, '有效 JD'],
                    ['解析', pipe.funnel.parsed, '结构化 JD'],
                  ].map(([label, value, unit], index) => (
                    <div key={label as string} className="relative rounded-xl border border-accent/14 bg-accent/6 p-3">
                      <div className="text-[11px] font-semibold text-accent-deep">{label}</div>
                      <div className="text-xl font-extrabold text-body-1 tabular-nums mt-1">{value as number}</div>
                      <div className="text-[10px] text-body-3">{unit}</div>
                      {index < 2 && <ArrowRight className="absolute top-1/2 -right-[9px] -translate-y-1/2 w-3 h-3 text-accent/45 z-10" />}
                    </div>
                  ))}
                </div>
              </section>
              <section aria-labelledby="relation-flow-title">
                <div id="relation-flow-title" className="text-xs font-bold text-body-1 mb-2">岗位能力关系 <span className="font-normal text-body-3">· 计量单位：项关系</span></div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    ['active', pipe.capability_relations?.active ?? pipe.funnel.validated_caps, '已验证', 'text-accent-deep bg-accent/8 border-accent/20'],
                    ['candidate', pipe.capability_relations?.candidate ?? pipe.employer_validation?.candidate_capabilities ?? 0, '待验证', 'text-warn bg-warn-weak border-warn/20'],
                    ['deprecated', pipe.capability_relations?.deprecated ?? 0, '已淘汰', 'text-body-2 bg-surface-muted border-line-soft/8'],
                  ].map(([state, value, label, cls]) => (
                    <div key={state as string} className={`rounded-xl border p-3 ${cls}`} title={`${state} 岗位能力关系`}>
                      <div className="text-[11px] font-semibold">{label}</div>
                      <div className="text-xl font-extrabold tabular-nums mt-1">{value as number}</div>
                      <div className="text-[10px] opacity-70">{state}</div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
            {!pipe.capability_relations && (
              <div className="mt-3 rounded-lg bg-warn-weak border border-warn/20 px-3 py-2 text-[11px] text-warn">当前数据服务未返回 candidate / deprecated 独立计数，页面不使用旧的“滤除数”拆分推测。</div>
            )}

            <div className="mt-5 pt-4 border-t border-line-soft/6">
              <div className="text-sm font-semibold text-body-1 mb-3">数据源采集台账</div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  {(() => {
                    const max = Math.max(1, ...pipe.platforms.map(p => p.count))
                    return pipe.platforms.slice(0, 8).map(p => (
                      <div key={p.platform} className="flex items-center gap-2.5">
                        <span className="w-20 shrink-0 truncate text-[11px] text-body-2 sm:w-28 sm:text-xs" title={p.platform}>{p.platform}</span>
                        <div className="flex-1 h-2 rounded-full bg-brand-ink/8 overflow-hidden">
                          <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.max(4, Math.round(p.count / max * 100))}%` }} />
                        </div>
                        <span className="text-[11px] text-body-2 tabular-nums w-10 text-right shrink-0">{p.count}</span>
                        <span className="text-[10px] text-body-3 w-20 text-right shrink-0 hidden sm:inline">{p.latest || ''}</span>
                      </div>
                    ))
                  })()}
                </div>
                <div className="space-y-1.5 max-h-[140px] overflow-auto pr-1 sm:max-h-[180px]">
                  {pipe.batches.map(b => (
                    <div key={b.batch_key} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-accent/6 px-3 py-1.5 text-[11px]">
                      <span className="font-medium text-body-1">{b.batch_key}</span>
                      <Badge tone={b.tier === 'official' ? 'emerald' : b.tier === 'dataset' ? 'cyan' : 'slate'}>{b.tier}</Badge>
                      <span className="flex-1" />
                      <span className="text-body-2 tabular-nums">入库 {b.kept}</span>
                      <span className="text-body-3 hidden sm:inline">{b.finished_at || ''}</span>
                    </div>
                  ))}
                  {pipe.batches.length === 0 && <div className="text-xs text-body-3 py-4 text-center">暂无批次记录</div>}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            {[
              ['多源数据采集', '招聘JD · 联网检索'],
              ['清洗·交叉验证', '去抄袭/通胀/时滞'],
              ['大模型抽取', '结构化能力项'],
              ['反幻觉聚合', '置信度+溯源'],
              ['图谱构建/演化', '动态更新'],
              ['匹配与诊断', '差距+学习路径'],
            ].map(([t, s], i) => (
              <div key={t} data-reveal className="relative rounded-xl bg-accent/6 border border-line-soft/8 p-3.5">
                <div className="text-[11px] text-accent font-bold mb-1">0{i + 1}</div>
                <div className="text-sm font-semibold text-body-1">{t}</div>
                <div className="text-[11px] text-body-3 mt-0.5">{s}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
