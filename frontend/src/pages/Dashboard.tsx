import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import {
  Briefcase, Sparkles, Layers, Database, ShieldCheck, Copy, TrendingUp, ArrowRight,
} from 'lucide-react'
import { api, Stats, JobListItem, CATEGORY_COLORS } from '../api'
import { Card, Spinner, ConfidencePill, Badge } from '../components/ui'

function Kpi({ icon, label, value, sub, tone, delay }: any) {
  return (
    <Card delay={delay} hover className="p-5 relative overflow-hidden">
      <div className="flex items-center justify-between">
        <div className="label">{label}</div>
        <div className={`w-9 h-9 rounded-lg grid place-items-center ${tone}`}>{icon}</div>
      </div>
      <div className="mt-3 text-3xl font-extrabold text-slate-900 tabular-nums">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </Card>
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const nav = useNavigate()

  useEffect(() => {
    api.stats().then(setStats)
    api.jobs({ size: 8 }).then(d => setJobs(d.items))
  }, [])

  if (!stats) return <Spinner />

  const catData = Object.entries(stats.categories).map(([name, value]) => ({
    name, value, itemStyle: { color: CATEGORY_COLORS[name] || '#64748B' },
  }))
  const donut = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: '#64748B' }, itemWidth: 10, itemHeight: 10 },
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
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-extrabold text-slate-900">数据驾驶舱</h1>
        <p className="text-slate-500 mt-1">多源异构数据驱动 · 岗位能力图谱构建与动态演化分析</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi delay={0} label="岗位总数" value={stats.total_jobs} sub={`新兴岗位 ${stats.new_jobs} 个`}
          icon={<Briefcase className="w-5 h-5 text-white" />} tone="bg-grad-accent" />
        <Kpi delay={0.05} label="技能点" value={stats.total_skills} sub="技能级粒度"
          icon={<Layers className="w-5 h-5 text-white" />} tone="bg-grad-violet" />
        <Kpi delay={0.1} label="已处理 JD" value={stats.total_jds} sub={`覆盖 ${Object.keys(stats.categories).length} 大技术栈`}
          icon={<Database className="w-5 h-5 text-white" />} tone="bg-cyan-500/80" />
        <Kpi delay={0.15} label="抄袭/重复拦截" value={stats.duplicate_jds} sub="交叉验证去噪"
          icon={<Copy className="w-5 h-5 text-white" />} tone="bg-rose-500/80" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card delay={0.1} className="p-5 lg:col-span-1">
          <div className="font-semibold text-slate-700 mb-1">技术栈分布</div>
          <ReactECharts option={donut} style={{ height: 260 }} />
        </Card>
        <Card delay={0.15} className="p-5">
          <div className="flex items-center gap-2 font-semibold text-slate-700">
            <ShieldCheck className="w-4 h-4 text-emerald-600" /> 图谱平均置信度
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
        <div className="flex items-center gap-2 font-semibold text-slate-700 mb-4">
          <TrendingUp className="w-4 h-4 text-cyan-600" /> 全流程闭环
        </div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          {[
            ['多源数据采集', '招聘JD · 联网检索'],
            ['清洗·交叉验证', '去抄袭/通胀/时滞'],
            ['大模型抽取', '结构化能力项'],
            ['反幻觉聚合', '置信度+溯源'],
            ['图谱构建/演化', '动态更新'],
            ['匹配与诊断', '差距+学习路径'],
          ].map(([t, s], i) => (
            <div key={t} className="relative rounded-xl bg-sky-50/70 border border-slate-200/70 p-3.5">
              <div className="text-[11px] text-accent font-bold mb-1">0{i + 1}</div>
              <div className="text-sm font-semibold text-slate-800">{t}</div>
              <div className="text-[11px] text-slate-400 mt-0.5">{s}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
