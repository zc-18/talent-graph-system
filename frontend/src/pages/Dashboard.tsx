import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Undo2 } from 'lucide-react'
import { IBriefcase, IStack, IDatabase, ICopy, IShieldCheck, ITrendUp } from '../components/icons'
import { api, Stats, JobListItem, PipelineStats, CATEGORY_COLORS } from '../api'
import { Card, ConfidencePill, Badge, PageSkeleton, ErrorState } from '../components/ui'
import { Kpi } from '../components/Kpi'
import { useReveal } from '../hooks/gsapFx'

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
  const qualityGauge = {
    series: [{
      type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 1, radius: '92%',
      progress: { show: true, width: 14, itemStyle: { color: '#0EA5E9' } },
      axisLine: { lineStyle: { width: 14, color: [[1, 'rgba(2,6,23,0.06)']] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      pointer: { show: false },
      detail: { valueAnimation: true, fontSize: 30, fontWeight: 800, color: '#0F172A',
        formatter: (v: number) => `${(v * 100).toFixed(1)}%`, offsetCenter: [0, 0] },
      data: [{ value: stats.avg_confidence }],
    }],
  }

  return (
    <div ref={revealRef} className="space-y-6">
      <div className="relative overflow-hidden rounded-2xl glass">
        <div aria-hidden className="absolute inset-0 pointer-events-none bg-cover bg-right"
          style={{ backgroundImage: 'url(/hero-dashboard.webp)' }} />
        <div className="relative z-10 px-6 py-7 sm:px-8 sm:py-9">
          <h1 className="text-3xl font-extrabold text-slate-900">数据驾驶舱</h1>
          <p className="text-slate-500 mt-1 max-w-xl">多源异构数据驱动 · 岗位能力图谱构建与动态演化分析</p>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi delay={0} label="岗位总数" value={stats.total_jobs} sub={`新兴岗位 ${stats.new_jobs} 个`}
          icon={<IBriefcase className="w-5 h-5 text-white" />} tone="bg-grad-accent" />
        <Kpi delay={0.05} label="技能点" value={stats.total_skills} sub="技能级粒度"
          icon={<IStack className="w-5 h-5 text-white" />} tone="bg-grad-violet" />
        <Kpi delay={0.1} label="已处理 JD" value={stats.total_jds} sub={`覆盖 ${Object.keys(stats.categories).length} 大技术栈`}
          icon={<IDatabase className="w-5 h-5 text-white" />} tone="bg-cyan-500/80" />
        <Kpi delay={0.15} label="抄袭/重复拦截" value={stats.duplicate_jds} sub="交叉验证去噪"
          icon={<ICopy className="w-5 h-5 text-white" />} tone="bg-rose-500/80" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card delay={0.1} className="p-5 lg:col-span-1">
          <div className="font-semibold text-slate-700 mb-1">技术栈分布</div>
          <ReactECharts option={donut} style={{ height: 260 }} />
        </Card>
        <Card delay={0.15} className="p-5">
          <div className="flex items-center gap-2 font-semibold text-slate-700">
            <IShieldCheck className="w-4 h-4 text-emerald-600" /> 图谱平均置信度
          </div>
          <ReactECharts option={qualityGauge} style={{ height: 260 }} />
          <div className="text-center text-xs text-slate-500 -mt-2">反幻觉交叉验证 · 来源加权可信度</div>
        </Card>
        <Card delay={0.2} className="p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold text-slate-700">岗位能力榜</div>
            <button onClick={() => nav('/jobs')} className="text-xs text-accent hover:underline flex items-center gap-1">
              全部 <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <div className="space-y-2 max-h-[260px] overflow-auto pr-1">
            {jobs.map(j => (
              <button key={j.id} onClick={() => nav(`/jobs/${j.id}`)}
                className="w-full flex items-center justify-between rounded-xl px-3 py-2.5 bg-sky-50/70 hover:bg-sky-100/80 transition text-left">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-800 truncate flex items-center gap-2">
                    {j.name}
                    {j.is_new && <Badge tone="amber">新兴</Badge>}
                  </div>
                  <div className="text-[11px] text-slate-400">{j.category} · {j.required_count} 项必备技能</div>
                </div>
                <ConfidencePill value={j.confidence} />
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card delay={0.25} className="p-6">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
          <div className="flex items-center gap-2 font-semibold text-slate-700">
            <ITrendUp className="w-4 h-4 text-cyan-600" /> 全流程闭环
          </div>
          {pipe && (
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <Undo2 className="w-3.5 h-3.5 text-amber-500" />
              人工反馈回流 <span className="font-bold text-amber-600 tabular-nums">{pipe.loop.manual_edits}</span> 次
              · 演化运行 <span className="font-bold text-cyan-600 tabular-nums">{pipe.loop.evolution_runs}</span> 轮
            </div>
          )}
        </div>
        {pipe ? (
          <>
            <div className="relative">
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                {[
                  ['采集', `${pipe.funnel.collected}`, '条原始 JD · 多源采集'],
                  ['清洗', `${pipe.funnel.after_dedup}`, '条去重后有效 JD'],
                  ['抽取', `${pipe.funnel.parsed}`, '条完成结构化解析'],
                  ['验证', `${pipe.funnel.validated_caps}`, `项能力通过交叉验证（滤除 ${pipe.funnel.filtered_caps}）`],
                  ['图谱', `${pipe.funnel.jobs} / ${pipe.funnel.skills}`, '岗位 / 技能点入图'],
                  ['应用', `${pipe.loop.evolution_runs}`, '轮演化 · 匹配诊断'],
                ].map(([t, v, s], i) => (
                  <div key={t as string} data-reveal className="relative rounded-xl bg-sky-50/70 border border-slate-200/70 p-3.5">
                    <div className="text-[11px] text-accent font-bold mb-1">0{i + 1} · {t}</div>
                    <div className="text-xl font-extrabold text-slate-900 tabular-nums">{v}</div>
                    <div className="text-[11px] text-slate-400 mt-0.5">{s}</div>
                    {i < 5 && <ArrowRight className="hidden md:block absolute top-1/2 -right-[13px] -translate-y-1/2 w-3.5 h-3.5 text-slate-300 z-10" />}
                  </div>
                ))}
              </div>
              {/* 人工反馈回流：从「应用」弯回「验证/图谱」的虚线指示 */}
              <div className="hidden md:flex items-center justify-end gap-2 mt-2 pr-2">
                <svg width="260" height="18" className="text-amber-400" aria-hidden>
                  <path d="M255 2 C 200 22, 60 22, 5 6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 3" />
                  <path d="M11 2 L4 6 L12 10" fill="none" stroke="currentColor" strokeWidth="1.5" />
                </svg>
                <span className="text-[11px] text-amber-600">人工反馈回流 {pipe.loop.manual_edits} 次（专家修订驱动图谱再验证）</span>
              </div>
            </div>

            <div className="mt-5 pt-4 border-t border-slate-100">
              <div className="text-sm font-semibold text-slate-700 mb-3">数据源采集台账</div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  {(() => {
                    const max = Math.max(1, ...pipe.platforms.map(p => p.count))
                    return pipe.platforms.slice(0, 8).map(p => (
                      <div key={p.platform} className="flex items-center gap-2.5">
                        <span className="text-xs text-slate-600 w-28 truncate shrink-0" title={p.platform}>{p.platform}</span>
                        <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                          <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.max(4, Math.round(p.count / max * 100))}%` }} />
                        </div>
                        <span className="text-[11px] text-slate-500 tabular-nums w-10 text-right shrink-0">{p.count}</span>
                        <span className="text-[10px] text-slate-400 w-20 text-right shrink-0 hidden sm:inline">{p.latest || ''}</span>
                      </div>
                    ))
                  })()}
                </div>
                <div className="space-y-1.5 max-h-[180px] overflow-auto pr-1">
                  {pipe.batches.map(b => (
                    <div key={b.batch_key} className="flex items-center gap-2 rounded-lg bg-sky-50/70 px-3 py-1.5 text-[11px]">
                      <span className="font-medium text-slate-700">{b.batch_key}</span>
                      <Badge tone={b.tier === 'official' ? 'emerald' : b.tier === 'dataset' ? 'cyan' : 'slate'}>{b.tier}</Badge>
                      <span className="flex-1" />
                      <span className="text-slate-500 tabular-nums">入库 {b.kept}</span>
                      <span className="text-slate-400 hidden sm:inline">{b.finished_at || ''}</span>
                    </div>
                  ))}
                  {pipe.batches.length === 0 && <div className="text-xs text-slate-400 py-4 text-center">暂无批次记录</div>}
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
              <div key={t} data-reveal className="relative rounded-xl bg-sky-50/70 border border-slate-200/70 p-3.5">
                <div className="text-[11px] text-accent font-bold mb-1">0{i + 1}</div>
                <div className="text-sm font-semibold text-slate-800">{t}</div>
                <div className="text-[11px] text-slate-400 mt-0.5">{s}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
