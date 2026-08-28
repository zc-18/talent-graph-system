import { useEffect, useState } from 'react'
import { Loader2, Plus, X, Wand2, ArrowRight, Layers, Clock, Target, BriefcaseBusiness } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { IGitBranch } from '../components/icons'
import { api, errMsg, JobListItem, JobLevels, CapChange, JobVersion, EvolutionTimeline } from '../api'
import { Card, Spinner, ConfidencePill, Badge, EmptyState } from '../components/ui'
import ChangeDiff from '../components/ChangeDiff'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useFloat } from '../hooks/gsapFx'
import { useAuth } from '../auth'
import { useReadOnly } from '../hooks/useReadOnly'

const SAMPLE_JD = `招聘高级Java开发工程师
岗位职责：负责核心交易系统研发，参与AI能力中台建设。
任职要求：
1. 精通Java、Spring Cloud微服务；
2. 熟悉分布式系统、高并发、消息队列；
3. 熟练使用 Kubernetes、Docker 容器化部署；
4. 了解大语言模型应用开发、RAG检索增强、向量数据库者优先；
5. 有云原生、Service Mesh 经验加分。`

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级' }
const LEVEL_ORDER = ['junior', 'middle', 'senior']

function dataSourceLabel(value: any): string {
  if (!value) return '未记录'
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return String(value)
  return Object.entries(value).map(([key, item]) => {
    if (key === 'employer_count') return `${item} 个独立雇主`
    if (key === 'jd_count') return `${item} 条 JD`
    return `${key}: ${typeof item === 'object' ? JSON.stringify(item) : item}`
  }).join(' · ') || '未记录'
}

function dateLabel(value?: string | null) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '尚无记录'
}

function TimelineOverview({ timeline }: { timeline: EvolutionTimeline }) {
  const lifecycle = [
    ['首次观察', timeline.first_observed_at],
    ['首次考证', timeline.first_evidenced_at],
    ['首次发布', timeline.first_published_at],
  ]
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div><div className="label">真实语料时间线</div><div className="mt-1 font-bold text-slate-900">{timeline.job_name}</div></div>
        <Badge tone={timeline.lifecycle_mode === 'first_observation' ? 'amber' : 'cyan'}>{timeline.lifecycle_mode === 'first_observation' ? '首次观察生命周期' : '历史演化生命周期'}</Badge>
      </div>
      <div className="mt-4 grid grid-cols-1 border-y border-slate-200 sm:grid-cols-3">
        {lifecycle.map(([label, value], index) => <div key={label} className={`py-3 ${index ? 'border-t border-slate-200 sm:border-l sm:border-t-0 sm:pl-4' : ''}`}><div className="text-[10px] font-semibold text-slate-400">{label}</div><div className="mt-1 text-sm font-bold tabular-nums text-slate-800">{dateLabel(value)}</div></div>)}
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {timeline.corpus_slices.map(slice => <div key={slice.year} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3"><div className="flex items-center justify-between gap-2"><b className="text-sm text-slate-800">{slice.label}</b><span className="text-[10px] text-slate-400">URL {Math.round(slice.url_coverage * 100)}%</span></div><div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500"><span>{slice.jd_count} 条 JD</span><span>{slice.employer_count} 个雇主</span><span>{slice.platforms.length} 个渠道</span></div></div>)}
        {timeline.corpus_slices.length === 0 && <div className="text-sm text-slate-400">暂无可核验语料切片</div>}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3"><span className="text-[11px] font-semibold text-slate-500">版本节点</span>{timeline.version_nodes.map(node => <span key={`${node.id}-${node.version}`} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600">v{node.version} · {node.status} · {node.change_count} 项变化</span>)}</div>
      <p className="mt-3 text-[11px] leading-5 text-slate-500">{timeline.coverage_note}</p>
    </Card>
  )
}

/** 级别演化视图：初级→中级→高级 阶梯式画像 + 相邻级别差异 */
function LevelEvolution({ jobId }: { jobId: number }) {
  const [levels, setLevels] = useState<JobLevels | null>(null)
  const [diffs, setDiffs] = useState<Record<string, CapChange[]>>({})
  const [error, setError] = useState(false)

  useEffect(() => {
    setLevels(null); setDiffs({}); setError(false)
    api.jobLevels(jobId).then(d => {
      setLevels(d)
      const avail = LEVEL_ORDER.filter(l => (d.available || []).includes(l))
      for (let i = 0; i < avail.length - 1; i++) {
        const frm = avail[i], to = avail[i + 1]
        api.levelDiff(jobId, frm, to)
          .then(r => setDiffs(prev => ({ ...prev, [`${frm}-${to}`]: r.changes || [] })))
          .catch(() => {})
      }
    }).catch(() => setError(true))
  }, [jobId])

  if (error) return <Card className="p-8 text-center text-sm text-slate-400">该岗位暂未构建分级画像</Card>
  if (!levels) return <Card className="p-5"><Spinner label="加载分级画像…" /></Card>
  const avail = LEVEL_ORDER.filter(l => (levels.available || []).includes(l))
  if (avail.length < 2) return <Card className="p-8 text-center text-sm text-slate-400">该岗位暂未构建分级画像</Card>

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
        {avail.map((lv, idx) => {
          const bucket = levels.levels?.[lv]
          const skills = (bucket?.skills || []).slice().sort((a, b) => b.weight - a.weight).slice(0, 10)
          return (
            <Card key={lv} delay={idx * 0.06} className={`p-5 ${idx === 1 ? 'lg:mt-6' : idx === 2 ? 'lg:mt-12' : ''}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className={`w-7 h-7 rounded-lg grid place-items-center text-accent-deep text-xs font-bold ${
                    idx === 0 ? 'bg-sky-400' : idx === 1 ? 'bg-grad-accent ring-1 ring-accent/20' : 'bg-grad-violet'}`}>{idx + 1}</span>
                  <span className="font-bold text-slate-800">{LEVEL_LABEL[lv] || lv}</span>
                </div>
                <span className="text-[11px] text-slate-400">{bucket?.jd_count ?? 0} 条 JD 支撑</span>
              </div>
              <div className="space-y-1.5">
                {skills.map(s => (
                  <div key={s.name} className="flex items-center gap-2 rounded-lg bg-sky-50/70 px-2.5 py-1.5">
                    <span className="text-xs font-medium text-slate-700 truncate">{s.name}</span>
                    <span className="flex-1" />
                    <span className="text-[10px] text-slate-400 tabular-nums shrink-0">权重 {Math.round(s.weight * 100)}%</span>
                    <span className="shrink-0"><ConfidencePill value={s.confidence} factors={s.factors} /></span>
                  </div>
                ))}
                {skills.length === 0 && <div className="text-xs text-slate-400 py-3 text-center">暂无数据</div>}
              </div>
            </Card>
          )
        })}
      </div>
      {avail.slice(0, -1).map((frm, i) => {
        const to = avail[i + 1]
        const changes = diffs[`${frm}-${to}`]
        return (
          <Card key={frm} className="p-5">
            <div className="label mb-3 flex items-center gap-2">
              <ArrowRight className="w-4 h-4 text-accent" />
              {LEVEL_LABEL[frm]} → {LEVEL_LABEL[to]} 能力跃迁
            </div>
            {!changes ? <div className="text-xs text-slate-400">对比中…</div> : changes.length === 0 ? (
              <div className="text-xs text-slate-400">两级别能力要求基本一致</div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {changes.map((c, j) => (
                  <div key={j} className="rounded-xl bg-sky-50/70 px-3 py-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-800">{c.skill_name}</span>
                      <ChangeDiff change={c} />
                    </div>
                    {c.reason && <p className="text-[11px] text-slate-500 mt-1">{c.reason}</p>}
                  </div>
                ))}
              </div>
            )}
          </Card>
        )
      })}
    </div>
  )
}

export default function Evolution() {
  const location = useLocation() as any
  const auth = useAuth()
  const readOnly = useReadOnly()
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [jobId, setJobId] = useState<number | null>(null)
  const [mode, setMode] = useState<'time' | 'level'>('time')
  const [jds, setJds] = useState<string[]>([SAMPLE_JD])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<any>(null)
  const [versions, setVersions] = useState<JobVersion[]>([])
  const [timeline, setTimeline] = useState<EvolutionTimeline | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const toast = useToast()
  const floatRef = useFloat<HTMLImageElement>({ y: 9, duration: 3.6, deps: [history, result, loading] })

  useEffect(() => {
    api.jobs({ size: 100 })
      .then(d => { setJobs(d.items); const pref = d.items.find(j => j.id === location.state?.jobId) || d.items.find(j => j.name === 'Java开发工程师') || d.items[0]; if (pref) setJobId(pref.id) })
      .catch(e => toast('error', errMsg(e, '岗位列表加载失败')))
  }, [])
  useEffect(() => { if (jobId) {
    setTimeline(null)
    api.changes(jobId).then(setHistory).catch(() => setHistory({ items: [] }))
    api.evolutionTimeline(jobId).then(setTimeline).catch(() => setTimeline(null))
    api.jobVersions(jobId).then(data => { const list = data.items || []; setVersions(list); setSelectedVersion(list[list.length - 1]?.version ?? list[0]?.version ?? null) }).catch(() => { setVersions([]); setSelectedVersion(null) })
    setResult(null)
  } }, [jobId])

  const run = async () => {
    if (!jobId) return
    setLoading(true)
    try {
      const r = await api.previewEvolution(jobId, jds.filter(j => j.trim()), true)
      setResult(r)
      toast('success', `变化预览完成（未写入图谱）：新增 ${r.evolution.added} · 删除 ${r.evolution.deleted} · 修改 ${r.evolution.modified}`)
    } catch (e) {
      // 失败保留已输入的 JD，便于修正后重试
      toast('error', errMsg(e, '演化失败，请稍后重试'))
    } finally { setLoading(false) }
  }


  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 shrink-0 rounded-xl bg-grad-accent ring-1 ring-accent/20 grid place-items-center shadow-glow">
          <IGitBranch className="w-6 h-6 text-accent-deep" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">既有岗位能力动态演化</h1>
          <p className="text-sm text-slate-500">用最新招聘 JD 驱动既有岗位能力更新，自动标注新增 / 删除 / 修改并溯源</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {[['time', '时间演化', Clock], ['level', '级别演化', Layers]].map(([k, label, Icon]: any) => (
          <button key={k} onClick={() => setMode(k)}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium whitespace-nowrap transition ${
              mode === k
                ? 'bg-grad-sky text-body-1 border border-accent/35 shadow-[0_2px_10px_-4px_rgb(var(--brand-accent)/0.45)]'
                : 'btn-ghost'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
        {mode === 'level' && (
          <div className="w-full sm:w-72 sm:ml-2">
            <Select value={jobId ?? ''} onChange={v => setJobId(Number(v))} label="选择岗位"
              options={jobs.map(j => ({ value: String(j.id), label: `${j.name}（${j.category}）` }))} />
          </div>
        )}
      </div>

      {mode === 'time' && timeline && <TimelineOverview timeline={timeline} />}

      {mode === 'level' ? (jobId ? <LevelEvolution jobId={jobId} /> : <Spinner />) : (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card className="p-5 space-y-3">
            <div className="label">选择待预览岗位</div>
          <Select value={jobId ?? ''} onChange={v => setJobId(Number(v))} label="选择待演化岗位"
            options={jobs.map(j => ({ value: String(j.id), label: `${j.name}（${j.category}）` }))} />
          {jobId && <div className="grid grid-cols-2 gap-2"><Link to={`/jobs/${jobId}`} className="btn-ghost text-xs"><BriefcaseBusiness className="w-3.5 h-3.5" /> 岗位画像</Link><Link to="/match" state={{ jobId }} className="btn-ghost text-xs"><Target className="w-3.5 h-3.5" /> 人岗匹配</Link></div>}
          {auth.can('admin') ? <>
          <div className="flex items-center justify-between">
            <div className="label">演化变化预览 · 最新 JD</div>
            <button onClick={() => setJds([...jds, ''])} className="text-xs text-accent flex items-center gap-1"><Plus className="w-3 h-3" /> 添加</button>
          </div>
          {jds.map((jd, i) => (
            <div key={i} className="relative">
              <textarea value={jd} onChange={e => { const n = [...jds]; n[i] = e.target.value; setJds(n) }}
                rows={i === 0 ? 8 : 4} className="input resize-none font-mono text-xs leading-relaxed"
                placeholder="粘贴招聘 JD 文本…" />
              {jds.length > 1 && (
                <button onClick={() => setJds(jds.filter((_, k) => k !== i))} aria-label={`删除第 ${i + 1} 条 JD`}
                  className="absolute top-2 right-2 text-slate-400 hover:text-rose-400 p-1 -m-1 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-rose-300"><X className="w-4 h-4" /></button>
              )}
            </div>
          ))}
          <button onClick={run} disabled={loading || !jobId} className="btn-primary w-full">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />} 预览能力变化
          </button>
          <p className="text-[11px] text-amber-600">
            此处仅生成变化预览，不写入公共图谱；新版本须通过管理员演化任务审核发布。
            {readOnly ? ' 当前环境为公共图谱只读模式。' : ''}
          </p>
          </> : (
            <div className="pt-2">
              <div className="label mb-2">完整版本快照</div>
              {versions.length === 0 ? <EmptyState text="该岗位暂无版本快照" /> : (
                <>
                  <div className="flex flex-wrap gap-2">
                    {versions.map(version => <button key={version.version} onClick={() => setSelectedVersion(version.version)} className={selectedVersion === version.version ? 'btn-primary !py-1.5' : 'btn-ghost !py-1.5'}>v{version.version}</button>)}
                  </div>
                  {(() => {
                    const version = versions.find(item => item.version === selectedVersion)
                    if (!version) return null
                    const clusters = version.contract?.clusters || version.clusters || []
                    return (
                      <div className="mt-4 rounded-xl bg-sky-50/70 p-4">
                        <div className="flex items-center justify-between gap-2"><Badge tone="cyan">{version.status}</Badge><span className="text-xs text-slate-400">{version.effective_at ? new Date(version.effective_at).toLocaleDateString() : typeof version.evidence_window === 'string' ? version.evidence_window : ''}</span></div>
                        <p className="text-sm text-slate-600 mt-3">{version.summary || `岗位 v${version.version} 完整能力快照`}</p>
                        <div className="flex flex-wrap gap-1.5 mt-3">{clusters.map(cluster => <Badge key={cluster.name} tone={cluster.importance === 'required' ? 'indigo' : 'amber'}>{cluster.name}</Badge>)}</div>
                      </div>
                    )
                  })()}
                </>
              )}
            </div>
          )}
        </Card>

        <Card className="p-5">
          {loading ? <Spinner label="解析新 JD 并交叉验证…" /> : result ? (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                {[['新增', result.evolution.added, 'emerald'], ['删除', result.evolution.deleted, 'rose'], ['修改', result.evolution.modified, 'amber']].map(
                  ([l, v, t]: any) => (
                    <div key={l} className="rounded-xl bg-sky-50/70 p-3 text-center">
                      <div className={`text-2xl font-extrabold ${t === 'emerald' ? 'text-accent-deep' : t === 'rose' ? 'text-rose-600' : 'text-amber-600'}`}>{v}</div>
                      <div className="text-xs text-slate-500">{l}能力项</div>
                    </div>
                  ))}
              </div>
              <div className="space-y-2 max-h-[280px] overflow-auto sm:max-h-[360px]">
                {result.changes.map((c: any, i: number) => (
                  <div key={i} className="rounded-xl bg-sky-50/70 px-3.5 py-2.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-slate-800">{c.skill_name}</span>
                      <ChangeDiff change={c} />
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{c.reason}</p>
                    <p className="text-[11px] text-slate-400 mt-1">数据源 · {dataSourceLabel(c.data_source)}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500">
              <div className="label mb-3">该岗位历史演化记录</div>
              {!history ? <Spinner /> : history.items.length === 0 ? (
                <div className="text-center py-6">
                  <img ref={floatRef} src="/decor-evolution.webp" alt=""
                    className="mx-auto mb-2 h-28 w-28 object-contain mix-blend-multiply drop-shadow-[0_12px_24px_rgba(56,189,248,0.22)] sm:h-40 sm:w-40" />
                  <div className="text-slate-400">暂无演化记录，提交新 JD 开始演化。</div>
                </div>
              ) : (
                <div className="space-y-2 max-h-[300px] overflow-auto sm:max-h-[420px]">
                  {history.items.slice(0, 20).map((c: any, i: number) => (
                    <div key={i} className="rounded-xl bg-sky-50/70 px-3.5 py-2.5">
                      <div className="flex items-center gap-2 text-xs flex-wrap">
                        <span className="font-medium text-slate-700">{c.skill_name}</span>
                        <ChangeDiff change={c} compact />
                        <span className="text-slate-400">v{c.version}</span>
                      </div>
                      <p className="text-[11px] text-slate-500 mt-1">{c.reason || '未记录变更原因'}</p>
                      <p className="text-[11px] text-slate-400 mt-1">数据源 · {dataSourceLabel(c.data_source)}</p>
                    </div>
                  ))}
                  {history.items.length > 20 && (
                    <div className="text-[11px] text-slate-400 pt-1">
                      显示前 20 条 / 共 {history.items.length} 条变更
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
      )}
    </div>
  )
}
