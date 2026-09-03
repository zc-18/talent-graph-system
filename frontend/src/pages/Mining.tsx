import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowRight, ArrowDownRight, ArrowUpRight, CalendarDays, Check, RotateCcw } from 'lucide-react'
import {
  IDatabase, ICorpus, IEvidence, IJob, ILightning, IPath, IShieldCheck, ISkill, ITrendUp,
} from '../components/icons'
import {
  api, miningReplay,
  type MiningDeltaType, type MiningFunnelStep, type MiningJobDelta, type MiningReplayFrame,
  type MiningRunDetail, type MiningRunsResponse, type MiningSkillDelta, type MiningTrendItem,
} from '../api'
import { Badge, Card, EmptyState, ErrorState, Meter, PageHeader, PageSkeleton, Spinner } from '../components/ui'
import { Kpi } from '../components/Kpi'
import ChangeDiff from '../components/ChangeDiff'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useGrowLine } from '../hooks/gsapFx'

/* ────────────────────────────────────────────────────────────────
   动态数据挖掘：每日一次的模拟聚合源采集回放。
   语料由竞赛主办方提供、以 BOSS直聘 形态呈现，但**不含雇主身份**，
   所以本页面所有"多样性"口径写的是「公司领域数」而不是雇主多样性，
   并且底部的门禁说明卡不可省略——它解释了这批技能点为什么只能是 candidate。
   ──────────────────────────────────────────────────────────────── */

/** 回放中的阶段视图 = 漏斗步骤 + 实时进度。 */
type StageView = MiningFunnelStep & {
  progress: number
  phase: 'pending' | 'running' | 'done'
  processed: number
  sample?: string
}

/** 已结算态：直接展示接口给出的最终漏斗（回放不可用时的兜底，也是首帧到达前的静态态） */
const settledStages = (funnel: MiningFunnelStep[]): StageView[] =>
  (funnel || []).map(s => ({ ...s, progress: 1, phase: 'done', processed: s.out, sample: undefined }))

/** 归零态：开始回放前把 out / dropped / reasons 清空，让数字真的从 0 跑起来 */
const resetStages = (funnel: MiningFunnelStep[]): StageView[] =>
  (funnel || []).map(s => ({
    ...s, out: 0, dropped: 0, reasons: {}, progress: 0, phase: 'pending', processed: 0, sample: undefined,
  }))

type LiveSummary = { new_skill_points: number; jobs_touched: number; skills_created: number }

/**
 * 把一帧 SSE 应用到组件状态。写在组件外、只接收 setter：
 * setState 全部走 updater 形式，因此即使被 useCallback 闭包捕获成"旧"引用也依然正确。
 */
function applyFrame(
  f: MiningReplayFrame,
  setStages: React.Dispatch<React.SetStateAction<StageView[]>>,
  setActive: React.Dispatch<React.SetStateAction<number>>,
  setSummary: React.Dispatch<React.SetStateAction<LiveSummary | null>>,
) {
  if (f.type === 'stage' && f.phase === 'begin') {
    setActive(typeof f.index === 'number' ? f.index : -1)
    setStages(prev => prev.map((s, i) => i === f.index
      ? { ...s, phase: 'running', in: f.in ?? s.in, progress: 0, processed: 0 } : s))
    return
  }
  if (f.type === 'stage' && f.phase === 'end') {
    setStages(prev => prev.map((s, i) => i === f.index
      ? {
        ...s, phase: 'done', progress: 1,
        in: f.in ?? s.in, out: f.out ?? s.out, dropped: f.dropped ?? s.dropped,
        reasons: f.reasons ?? s.reasons, detail: f.detail ?? s.detail,
        processed: f.out ?? s.processed,
      } : s))
    return
  }
  if (f.type === 'tick') {
    setStages(prev => prev.map((s, i) => i === f.index
      ? {
        // 进度只许前进：帧乱序或重复时不要让进度条倒退
        ...s, progress: Math.max(s.progress, f.progress ?? 0),
        processed: f.processed ?? s.processed,
        sample: f.sample || s.sample,
      } : s))
    return
  }
  if (f.type === 'summary') {
    setSummary({
      new_skill_points: f.new_skill_points ?? 0,
      jobs_touched: f.jobs_touched ?? 0,
      skills_created: f.skills_created ?? 0,
    })
    return
  }
  if (f.type === 'done') {
    setActive(-1)
    setStages(prev => prev.map(s => ({ ...s, phase: 'done', progress: 1 })))
  }
}

/**
 * 单岗位明细的渲染上限。后端 `MAX_DELTAS_PER_JOB = 40` 已经截过一刀，这里是第二道闸：
 * 真实一天能产出上千条 delta，一旦后端放宽上限，这一层保证 DOM 不会被瞬间撑爆。
 * 与后端取同值，所以今天渲染结果完全不变；真的咬到了会在下面明确写出总数，不静默隐藏。
 */
const DELTA_RENDER_CAP = 40

const DELTA_LABEL: Record<MiningDeltaType, string> = {
  new: '新增', support_up: '支持增强', support_down: '支持减弱', vanished: '今日消失',
}
const DELTA_TONE: Record<MiningDeltaType, string> = {
  new: 'cyan', support_up: 'emerald', support_down: 'amber', vanished: 'rose',
}

/**
 * 把挖掘侧的技能点变化整形成 ChangeDiff 的入参，而不是另写一套 diff 标记。
 * modify 一律带 compact：ChangeDiff 在没有可渲染字段差异时，非 compact 会退化成
 * 「→（细节调整）」这种空话，compact 下只留徽标，支持数变化由本页自己的箭头胶囊承担。
 */
function toChange(d: MiningSkillDelta) {
  if (d.delta_type === 'new') return { change_type: 'add', new_value: { status: d.curr_status } }
  if (d.delta_type === 'vanished') return { change_type: 'delete', old_value: { status: d.prev_status } }
  return { change_type: 'modify', old_value: { status: d.prev_status }, new_value: { status: d.curr_status } }
}

/** 图谱状态的中文名，与 ChangeDiff 的 STATUS 表保持一致，不要在页面上裸露英文枚举 */
const STATUS_LABEL: Record<string, string> = {
  active: '确认能力项', candidate: '候选能力项', deprecated: '已淘汰',
}

/**
 * 「仅观测·未入图」判定。
 *
 * 挖掘服务只把能挂到某个粗概念下的技能名建成 Skill 节点；挂不上的名字不进图谱，
 * 只留一条观测记录（`skill_id` 为 null、`curr_status` 为 null）。这种行必须显式说明，
 * 否则状态列直接消失，读者会误以为它和其他 candidate 一样已经入图。
 *
 * 判定优先级（后端已同时下发 in_graph 与 skill_id，实测两者恒等价）：
 *   in_graph 有值       → 直接采信，这是权威字段；
 *   skill_id === null   → 明确没有节点；
 *   skill_id 有值       → 有节点；
 *   两个都没下发        → 退化为看 curr_status（老接口兼容）。
 * `vanished` 例外：它本来就是"离开图谱"，curr_status 为空是语义正确，不是未入图。
 */
function isObservationOnly(d: Pick<MiningSkillDelta, 'delta_type' | 'skill_id' | 'in_graph' | 'curr_status'>): boolean {
  if (d.delta_type === 'vanished') return false
  if (typeof d.in_graph === 'boolean') return !d.in_graph
  if (d.skill_id === null) return true
  if (d.skill_id != null) return false
  return !d.curr_status
}

/** 支持数 旧 → 新 胶囊。上升走 success、下降走 warn，和徽标色系对齐 */
function SupportArrow({ from, to }: { from: number; to: number }) {
  const up = to >= from
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line-soft/8 bg-white/70 px-2 py-0.5 text-[11px]">
      <span className="text-body-3">支持</span>
      <span className="tabular-nums text-body-2">{from}</span>
      {up ? <ArrowUpRight className="h-3 w-3 text-success" /> : <ArrowDownRight className="h-3 w-3 text-warn" />}
      <span className={`font-semibold tabular-nums ${up ? 'text-success' : 'text-warn'}`}>{to}</span>
    </span>
  )
}

export default function Mining() {
  const nav = useNavigate()
  const toast = useToast()

  const [runs, setRuns] = useState<MiningRunsResponse | null>(null)
  const [runDate, setRunDate] = useState('')
  const [detail, setDetail] = useState<MiningRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailErr, setDetailErr] = useState(false)
  const [trend, setTrend] = useState<MiningTrendItem[]>([])
  const [error, setError] = useState(false)
  const [reloadTick, setReloadTick] = useState(0)

  const [stages, setStages] = useState<StageView[]>([])
  const [active, setActive] = useState(-1)
  const [summary, setSummary] = useState<LiveSummary | null>(null)
  const [playing, setPlaying] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // 卸载时掐断流：miningReplay 的 read() 会抛 AbortError，由 startReplay 的 catch 吞掉，
  // 之后所有 setState 都被 ac.signal.aborted 挡住，不会有卸载后写 state 的告警。
  useEffect(() => () => { abortRef.current?.abort() }, [])

  const startReplay = useCallback(async (d: MiningRunDetail) => {
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setStages(resetStages(d.funnel))
    setSummary(null)
    setActive(-1)
    setPlaying(true)
    try {
      await miningReplay(d.run.run_date, f => {
        if (!ac.signal.aborted) applyFrame(f, setStages, setActive, setSummary)
      }, ac.signal)
      if (!ac.signal.aborted) setStages(prev => prev.map(s => ({ ...s, phase: 'done', progress: 1 })))
    } catch {
      // abort 是我们自己发的（切日期 / 卸载 / 重播），不是错误
      if (ac.signal.aborted) return
      setStages(settledStages(d.funnel))
      setActive(-1)
      toast('info', '采集回放流不可用，已直接展示当日最终漏斗')
    } finally {
      if (!ac.signal.aborted) setPlaying(false)
    }
  }, [toast])

  const loadRuns = useCallback(() => {
    setError(false)
    setRuns(null)
    api.miningRuns(30).then(d => {
      setRuns(d)
      // 失败批次留在台账供排查，但不能抢占公开页默认展示的最近可信结果。
      const first = d.items.find(i => i.status === 'completed')?.run_date || d.items?.[0]?.run_date
      if (first) setRunDate(prev => (prev && d.items.some(i => i.run_date === prev) ? prev : first))
    }).catch(() => setError(true))
    api.miningSkillTrend(30).then(d => setTrend(d.items || [])).catch(() => setTrend([]))
  }, [])
  useEffect(() => { loadRuns() }, [loadRuns])

  // 选中日期变化 → 拉详情 → 自动开播一次
  useEffect(() => {
    if (!runDate) return
    let cancelled = false
    setDetailLoading(true)
    setDetailErr(false)
    setDetail(null)
    setStages([])
    api.miningRun(runDate)
      .then(d => {
        if (cancelled) return
        setDetail(d)
        setStages(settledStages(d.funnel))
        if (d.run.status === 'completed') void startReplay(d)
        else setPlaying(false)
      })
      .catch(() => { if (!cancelled) setDetailErr(true) })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [runDate, reloadTick, startReplay])

  const trainingItems = useMemo(() => {
    const out: { job: MiningJobDelta; delta: MiningSkillDelta }[] = []
    for (const j of detail?.jobs || []) {
      for (const d of j.deltas || []) {
        if (d.delta_type === 'new' && d.training_plan?.length) out.push({ job: j, delta: d })
      }
    }
    return out
  }, [detail])

  /**
   * 入图 / 仅观测 的条数分布。**只统计真正渲染出来的行**：后端按 MAX_DELTAS_PER_JOB
   * 截断过，本页再按 DELTA_RENDER_CAP 截一次，所以这不是当日全量，文案必须写「本页」。
   */
  const graphSplit = useMemo(() => {
    let shown = 0, observed = 0
    for (const j of detail?.jobs || []) {
      for (const d of (j.deltas || []).slice(0, DELTA_RENDER_CAP)) {
        shown += 1
        if (isObservationOnly(d)) observed += 1
      }
    }
    return { shown, observed, inGraph: shown - observed }
  }, [detail])

  const trendOption = useMemo(() => ({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, textStyle: { color: '#64748B', fontSize: 11 }, itemWidth: 10, itemHeight: 10, itemGap: 14 },
    grid: { left: 8, right: 8, top: 26, bottom: 32, containLabel: true },
    xAxis: {
      type: 'category', data: trend.map(t => (t.run_date || '').slice(5)),
      axisLabel: { color: '#64748B', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(35,48,92,0.12)' } }, axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '每日新增', nameTextStyle: { color: '#64748B', fontSize: 10 },
        axisLabel: { color: '#64748B', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(35,48,92,0.06)' } },
      },
      {
        type: 'value', name: '累计', nameTextStyle: { color: '#64748B', fontSize: 10 },
        axisLabel: { color: '#64748B', fontSize: 10 }, splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '每日新增技能点', type: 'bar', barMaxWidth: 18,
        itemStyle: { color: '#5B7CF0', borderRadius: [4, 4, 0, 0] },
        data: trend.map(t => t.new_skill_points),
      },
      {
        name: '累计新增', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
        lineStyle: { color: '#A78BFA', width: 2 }, itemStyle: { color: '#8B6FE8' },
        areaStyle: { color: 'rgba(167,139,250,0.12)' },
        data: trend.map(t => t.cumulative_new),
      },
    ],
  }), [trend])

  const railRef = useGrowLine<HTMLDivElement>('.mining-rail-line')
  const pathRef = useGrowLine<HTMLDivElement>('.mining-path-line')

  if (error) return <ErrorState text="动态数据挖掘数据加载失败" onRetry={loadRuns} />
  if (!runs) return <PageSkeleton />

  const run = detail?.run
  const today = runs.items.find(i => i.status === 'completed') || runs.items[0]
  const kpiSrc = run || today
  const dateOptions = runs.items.map(i => ({
    value: i.run_date,
    label: `${i.run_date}${i.status === 'failed' ? ' · 已阻断' : i.dry_run ? ' · 试运行' : ''}`,
  }))

  const header = (
    <PageHeader
      icon={<IDatabase className="h-6 w-6" />}
      title="动态数据挖掘"
      subtitle={`${runs.source_label} · ${runs.schedule} · 每日预算 ¥${runs.daily_budget_cny}`}
      action={
        <div className="flex items-center gap-2">
          <Select value={runDate} onChange={setRunDate} options={dateOptions}
            className="w-[190px]" label="选择挖掘批次日期" placeholder="选择日期"
            icon={<CalendarDays className="h-4 w-4" />} align="right" />
          <button className="btn-primary text-sm"
            disabled={!detail || playing || run?.status !== 'completed'}
            onClick={() => detail && void startReplay(detail)}>
            <RotateCcw className={`h-4 w-4 ${playing ? 'animate-spin' : ''}`} />
            {playing ? '回放中' : '重新播放'}
          </button>
        </div>
      } />
  )

  // 夜间任务还没跑过：不要拿一堆 0 冒充数据
  if (runs.items.length === 0) {
    return (
      <div className="space-y-5">
        {header}
        <Card className="p-6">
          <div className="text-center">
            <img src="/empty-discovery.webp" alt=""
              className="mx-auto mb-2 h-28 w-28 object-contain mix-blend-multiply sm:h-40 sm:w-40" />
            <EmptyState text="尚无挖掘批次记录"
              hint={`${runs.source_label} 的每日任务（${runs.schedule}）还未产出结果，明日 00:00 后再来查看。`} />
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="border-b border-line-soft/8 pb-5">{header}</div>

      {/* 1 · KPI ------------------------------------------------------------ */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi delay={0} label="今日采集" value={kpiSrc?.rows_read ?? 0} unit="行"
          sub={`游标 ${run?.cursor_start ?? '—'} – ${run?.cursor_end ?? '—'}`}
          icon={<ICorpus className="h-5 w-5" />} tone="bg-grad-accent" />
        <Kpi delay={0.05} label="有效语料" value={kpiSrc?.rows_valid ?? 0} unit="行"
          sub={`去重后 ${kpiSrc?.rows_dedup ?? 0} 行`}
          ring={kpiSrc?.rows_read ? (kpiSrc.rows_valid / kpiSrc.rows_read) : 0}
          icon={<IEvidence className="h-5 w-5" />} tone="bg-grad-violet" />
        <Kpi delay={0.1} label="命中岗位" value={summary?.jobs_touched ?? kpiSrc?.jobs_touched ?? 0} unit="个"
          sub={`归一命中 ${kpiSrc?.rows_mapped ?? 0} 行`}
          icon={<IJob className="h-5 w-5" />} tone="bg-grad-accent" />
        <Kpi delay={0.15} label="新增技能点" value={summary?.new_skill_points ?? kpiSrc?.new_skill_points ?? 0} unit="个"
          sub={`新建技能 ${summary?.skills_created ?? kpiSrc?.skills_created ?? 0} · 证据 ${kpiSrc?.evidence_created ?? 0}`}
          icon={<ISkill className="h-5 w-5" />} tone="bg-grad-violet" />
      </div>

      {run?.status === 'failed' && (
        <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger/25 bg-danger-weak px-4 py-3 text-danger">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="min-w-0">
            <div className="text-sm font-semibold">该批次未通过数据质量门，未生成日间变化</div>
            <div className="mt-1 break-words text-xs leading-relaxed opacity-80">
              {run.error || '任务已中止，线上继续保留最近一次成功批次。'}
            </div>
          </div>
        </div>
      )}

      {detailErr && (
        <Card className="p-5">
          <ErrorState text={`${runDate} 的挖掘详情加载失败`} onRetry={() => setReloadTick(t => t + 1)} />
        </Card>
      )}

      {detailLoading && !detail && <Card className="p-5"><Spinner label="加载当日挖掘批次…" /></Card>}

      {detail && (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
            {/* 2 · 采集回放 -------------------------------------------------- */}
            <Card delay={0.1} className="p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2 font-semibold text-body-1">
                    <ILightning className="h-4 w-4 text-accent-deep" /> 采集回放
                  </div>
                  <div className="mt-0.5 text-xs text-body-3">
                    {run?.run_date} · 分片 {run?.shard_index ?? 0} · 大模型调用 {run?.llm_calls ?? 0} 次 / ¥{(run?.llm_cost_cny ?? 0).toFixed(3)}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {playing
                    ? <Badge tone="cyan">回放中 {Math.min(stages.length, active + 1)}/{stages.length}</Badge>
                    : run?.status === 'failed'
                      ? <Badge tone="rose">质量闸已阻断</Badge>
                      : <Badge tone="emerald">已完成</Badge>}
                  {run?.llm_budget_hit && <Badge tone="amber">触及预算上限</Badge>}
                  {run?.dry_run && <Badge tone="slate">试运行</Badge>}
                </div>
              </div>

              <div ref={railRef} className="relative pl-6">
                <div className="mining-rail-line absolute bottom-1 left-[7px] top-1 w-px bg-gradient-to-b from-accent to-accent/40" />
                {stages.map((s, i) => (
                  <div key={s.key} className="relative pb-4 last:pb-0">
                    <span className={`absolute -left-[19px] top-0.5 grid h-3.5 w-3.5 place-items-center rounded-full text-[8px] font-bold text-white ring-4 ring-white
                      ${s.phase === 'done' ? 'bg-grad-accent' : s.phase === 'running' ? 'bg-accent-violet animate-pulse' : 'bg-brand-ink/18'}`}>
                      {s.phase === 'done' ? <Check className="h-2.5 w-2.5" /> : i + 1}
                    </span>
                    <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
                      <span className={`text-sm font-semibold ${s.phase === 'pending' ? 'text-body-3' : 'text-body-1'}`}>{s.label}</span>
                      <span className="text-[11px] tabular-nums text-body-2">
                        {s.in}
                        <ArrowRight className="mx-1 inline h-3 w-3 text-body-3" />
                        <b className="text-body-1">{s.phase === 'done' ? s.out : s.processed}</b>
                        {s.dropped > 0 && <span className="ml-1.5 text-danger">−{s.dropped}</span>}
                      </span>
                    </div>
                    <div className="mt-1.5">
                      <Meter value={s.progress} tone={s.phase === 'pending' ? 'bg-brand-ink/18' : 'bg-grad-fill'} min={0} />
                    </div>
                    <div className="mt-1 truncate text-[11px] text-body-3" title={s.sample || s.detail || ''}>
                      {s.phase === 'running' && s.sample ? `正在处理：${s.sample}` : (s.detail || ' ')}
                    </div>
                  </div>
                ))}
                {stages.length === 0 && <EmptyState text="当日无阶段记录" />}
              </div>
            </Card>

            {/* 3 · 今日漏斗 -------------------------------------------------- */}
            <Card delay={0.15} className="p-5">
              <div className="flex items-center gap-2 font-semibold text-body-1">
                <ICorpus className="h-4 w-4 text-accent-deep" /> 今日漏斗
              </div>
              <div className="mt-0.5 text-xs text-body-3">每一步的进出量与被丢弃的原因分布</div>
              <div className="mt-4 space-y-2">
                {(detail.funnel || []).map(s => {
                  const reasons = Object.entries(s.reasons || {})
                  return (
                    <div key={s.key} className="rounded-xl border border-line-soft/6 bg-accent/6 px-3.5 py-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                        <span className="text-sm font-medium text-body-1">{s.label}</span>
                        <span className="text-xs tabular-nums text-body-2">
                          {s.in}
                          <ArrowRight className="mx-1 inline h-3 w-3 text-accent/45" />
                          <b className="text-accent-deep">{s.out}</b>
                          {s.dropped > 0 && <span className="ml-1.5 text-danger">丢弃 {s.dropped}</span>}
                        </span>
                      </div>
                      {reasons.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {reasons.map(([reason, count]) => (
                            <span key={reason}
                              className="inline-flex items-center gap-1 rounded-full border border-danger/25 bg-danger-weak px-2 py-0.5 text-[11px] text-danger">
                              {reason}<b className="tabular-nums">{count}</b>
                            </span>
                          ))}
                        </div>
                      )}
                      {typeof s.duration_ms === 'number' && (
                        <div className="mt-1 text-[11px] tabular-nums text-body-3">耗时 {s.duration_ms} ms</div>
                      )}
                    </div>
                  )
                })}
                {(detail.funnel || []).length === 0 && <EmptyState text="当日无漏斗记录" />}
              </div>
            </Card>
          </div>

          {/* 4 · 岗位技能点变化 ---------------------------------------------- */}
          <Card delay={0.2} className="p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="flex items-center gap-2 font-semibold text-body-1">
                  <ISkill className="h-4 w-4 text-accent-deep" /> 岗位技能点变化
                </div>
                <div className="mt-0.5 text-xs text-body-3">与前一日相比，各岗位得到与失去的技能点</div>
                {graphSplit.shown > 0 && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-body-3">
                    <span>本页 <b className="tabular-nums text-body-2">{graphSplit.shown}</b> 条明细</span>
                    <span className="inline-flex items-center gap-1">
                      <i className="h-1.5 w-1.5 rounded-full bg-grad-accent" aria-hidden />
                      已入图 <b className="tabular-nums text-body-2">{graphSplit.inGraph}</b>
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <i className="h-1.5 w-1.5 rounded-full bg-brand-ink/25" aria-hidden />
                      仅观测·未入图 <b className="tabular-nums text-body-2">{graphSplit.observed}</b>
                    </span>
                    <span className="text-body-3/80">（技能词需与粗粒度概念共现才建图谱节点）</span>
                  </div>
                )}
              </div>
              {detail.top_skills?.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-body-3">今日热点</span>
                  {detail.top_skills.slice(0, 5).map(t => {
                    const obs = isObservationOnly(t)
                    return (
                      <span key={`${t.job_id}-${t.name}`}
                        title={obs ? '该技能词未入图，仅记录在观测层' : `${t.job_name} · ${DELTA_LABEL[t.delta_type] || t.delta_type}`}>
                        <Badge tone={obs ? 'slate' : (DELTA_TONE[t.delta_type] || 'slate')}>
                          {t.name} <b className="tabular-nums">{t.count}</b>
                          {obs && <span className="ml-1 opacity-70">仅观测</span>}
                        </Badge>
                      </span>
                    )
                  })}
                </div>
              )}
            </div>

            {(detail.jobs || []).length === 0 ? (
              <EmptyState text="当日没有岗位发生技能点变化" hint="通常意味着新语料的技能点都已在图谱中且支持数持平。" />
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {detail.jobs.map(j => (
                  <div key={j.job_id} className="rounded-2xl border border-line-soft/6 bg-surface-muted p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <button onClick={() => nav(`/jobs/${j.job_id}`)}
                        className="min-w-0 text-left text-sm font-semibold text-body-1 hover:text-accent-deep hover:underline">
                        {j.job_name}
                      </button>
                      <div className="flex items-center gap-1.5">
                        {j.category && <Badge tone="slate">{j.category}</Badge>}
                        <span className="text-[11px] tabular-nums text-body-3">{j.rows} 行语料</span>
                      </div>
                    </div>

                    <div className="mt-3 grid grid-cols-4 gap-1.5">
                      {([
                        ['新增', j.new_count, 'text-accent-deep'],
                        ['增强', j.support_up, 'text-success'],
                        ['减弱', j.support_down, 'text-warn'],
                        ['消失', j.vanished, 'text-danger'],
                      ] as const).map(([label, value, cls]) => (
                        <div key={label} className="rounded-lg border border-line-soft/6 bg-white px-2 py-2 text-center">
                          <div className={`text-lg font-extrabold tabular-nums ${cls}`}>{value ?? 0}</div>
                          <div className="text-[10px] text-body-3">{label}</div>
                        </div>
                      ))}
                    </div>

                    <div className="mt-3 max-h-[268px] space-y-2 overflow-auto pr-1">
                      {(j.deltas || []).slice(0, DELTA_RENDER_CAP).map(d => (
                        <div key={`${d.skill_name}-${d.delta_type}`} className="rounded-xl bg-white px-3 py-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="text-sm font-semibold text-body-1">{d.skill_name}</span>
                            <ChangeDiff change={toChange(d)} compact />
                            {isObservationOnly(d) && <Badge tone="slate">仅观测·未入图</Badge>}
                            {d.delta_type !== 'new' && <SupportArrow from={d.prev_support} to={d.curr_support} />}
                            {d.delta_type === 'new' && (
                              <span className="text-[11px] tabular-nums text-body-2">支持 {d.curr_support}</span>
                            )}
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-body-3">
                            <span>{DELTA_LABEL[d.delta_type] || d.delta_type}</span>
                            {/* 模拟源无雇主身份，这里只能叫公司领域数，绝不能写成雇主多样性 */}
                            <span title="该技能点在多少个公司领域中出现（模拟源不含雇主身份，不能作为雇主多样性）">
                              公司领域数 <b className="tabular-nums text-body-2">{d.industry_count ?? 0}</b>
                            </span>
                            {isObservationOnly(d)
                              ? <span title="该技能名未能归属到任何粗粒度能力概念，因此没有图谱节点，仅保留观测记录，不计入岗位能力集合">
                                  未建立图谱节点
                                </span>
                              : d.curr_status
                                ? <span>状态 {STATUS_LABEL[d.curr_status] || d.curr_status}</span>
                                : null}
                          </div>
                          {d.industries?.length ? (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {d.industries.slice(0, 4).map(x => <span key={x} className="chip">{x}</span>)}
                            </div>
                          ) : null}
                          {d.sample_titles?.length ? (
                            <div className="mt-1 truncate text-[11px] text-body-3" title={d.sample_titles.join(' / ')}>
                              样例：{d.sample_titles.slice(0, 2).join(' / ')}
                            </div>
                          ) : null}
                        </div>
                      ))}
                      {(j.deltas || []).length === 0 && <div className="py-3 text-center text-xs text-body-3">无明细</div>}
                      {(j.truncated || (j.deltas || []).length > DELTA_RENDER_CAP) && (
                        <div className="pt-1 text-[11px] text-body-3">
                          明细已截断：当日共 <b className="tabular-nums text-body-2">{j.deltas_total ?? (j.deltas || []).length}</b> 条变化，
                          此处展示前 <b className="tabular-nums text-body-2">{Math.min((j.deltas || []).length, DELTA_RENDER_CAP)}</b> 条
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {detail.jobs_truncated && (
              <div className="mt-3 text-[11px] text-body-3">
                当日共 <b className="tabular-nums text-body-2">{detail.jobs_total ?? detail.jobs.length}</b> 个岗位发生技能点变化，
                按新增数排序展示前 <b className="tabular-nums text-body-2">{detail.jobs.length}</b> 个。
              </div>
            )}
          </Card>

          {/* 5 · 新技能点 → 新人培训路径 -------------------------------------- */}
          <Card delay={0.25} className="p-5">
            <div className="flex items-center gap-2 font-semibold text-body-1">
              <IPath className="h-4 w-4 text-accent-deep" /> 新技能点 · 新人培训路径
            </div>
            <div className="mt-0.5 text-xs text-body-3">对当日新发现的技能点自动生成的入门顺序，先修项来自技能前置关系</div>
            {trainingItems.length === 0 ? (
              <div className="mt-3"><EmptyState text="当日没有需要新建培训路径的技能点" /></div>
            ) : (
              <div ref={pathRef} className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
                {trainingItems.slice(0, 6).map(({ job, delta }) => (
                  <div key={`${job.job_id}-${delta.skill_name}`} className="rounded-2xl border border-line-soft/6 bg-surface-muted p-4">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone="cyan">新技能点</Badge>
                      <span className="text-sm font-semibold text-body-1">{delta.skill_name}</span>
                      <span className="text-[11px] text-body-3">来自 {job.job_name}</span>
                    </div>
                    <div className="relative mt-3 pl-6">
                      <div className="mining-path-line absolute bottom-1 left-[7px] top-1 w-px bg-gradient-to-b from-accent to-accent/40" />
                      {(delta.training_plan || []).map(p => (
                        <div key={p.step} className="relative pb-3.5 last:pb-0">
                          <span className="absolute -left-[19px] top-0.5 grid h-3.5 w-3.5 place-items-center rounded-full bg-grad-accent text-[8px] font-bold text-white ring-4 ring-white tabular-nums">{p.step}</span>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold text-body-1">{p.skill}</span>
                            {p.priority && <Badge tone={p.priority === '高' ? 'rose' : 'amber'}>{p.priority}优先</Badge>}
                            {p.category && <Badge tone="slate">{p.category}</Badge>}
                          </div>
                          {p.prerequisites?.length ? (
                            <p className="mt-0.5 text-[11px] text-body-3">先修：{p.prerequisites.join('、')}</p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {trainingItems.length > 6 && (
              <div className="mt-3 text-[11px] text-body-3">共 {trainingItems.length} 条新技能点培训路径，此处展示前 6 条。</div>
            )}
          </Card>
        </>
      )}

      {/* 6 · 趋势 --------------------------------------------------------- */}
      <Card delay={0.3} className="p-5">
        <div className="flex items-center gap-2 font-semibold text-body-1">
          <ITrendUp className="h-4 w-4 text-accent-deep" /> 挖掘趋势
        </div>
        <div className="mt-0.5 text-xs text-body-3">每日新增技能点（柱）与累计新增（线）</div>
        {trend.length === 0
          ? <div className="mt-3"><EmptyState text="暂无多日趋势数据" hint="至少需要一个已完成的挖掘批次。" /></div>
          : <ReactECharts option={trendOption} style={{ height: 280 }} />}
      </Card>

      {/* 7 · 合规与门禁说明（不可省略） -------------------------------------- */}
      <Card delay={0.35} className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <IShieldCheck className="h-4 w-4 text-accent-deep" />
          <span className="font-semibold text-body-1">合规与门禁说明</span>
          <Badge tone="amber">离线模拟聚合源</Badge>
          <Badge tone="slate">无雇主身份</Badge>
          <Badge tone="violet">candidate 入图</Badge>
        </div>
        {detail?.gate_note && (
          <p className="mt-3 rounded-xl border border-warn/25 bg-warn-weak px-3.5 py-2.5 text-sm leading-relaxed text-warn">
            {detail.gate_note}
          </p>
        )}
        <ul className="mt-3 space-y-1.5 text-sm leading-relaxed text-body-2">
          <li>
            · 本页数据源在界面上呈现为 <b className="text-body-1">{runs.source_label}</b>，但实际语料是竞赛主办方提供的
            <b className="text-body-1">离线模拟聚合源</b>（platform <code className="tabular-nums">{runs.platform}</code>，tier
            <code> {runs.tier}</code>），并非对该平台的实时抓取。
          </li>
          <li>
            · 该语料<b className="text-body-1">不携带雇主身份</b>。系统的交叉验证门禁以「独立雇主」而非「独立平台」计数，
            雇主缺失的语料无法满足 ≥2 个独立雇主的条件。
          </li>
          <li>
            · 因此本源产出的技能点一律以 <b className="text-body-1">candidate（候选能力项）</b>状态入图，
            不会被计为 active 已验证能力，也不参与岗位置信度中的来源多样性因子。
            页面中的多样性口径写作<b className="text-body-1">公司领域数</b>，不等同于雇主多样性。
          </li>
          <li>
            · 每日运行有成本闸：预算 ¥{runs.daily_budget_cny}，触顶即停止调用大模型并在当日批次上标记
            <code> llm_budget_hit</code>，剩余语料顺延到次日分片。
          </li>
        </ul>
      </Card>
    </div>
  )
}
