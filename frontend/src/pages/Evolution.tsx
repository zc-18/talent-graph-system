import { useEffect, useState } from 'react'
import { Loader2, Plus, X, Wand2, ArrowRight, Layers, Clock } from 'lucide-react'
import { IGitBranch } from '../components/icons'
import { api, errMsg, JobListItem, JobLevels, CapChange } from '../api'
import { Card, Spinner, ConfidencePill } from '../components/ui'
import ChangeDiff from '../components/ChangeDiff'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useFloat } from '../hooks/gsapFx'

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
                  <span className={`w-7 h-7 rounded-lg grid place-items-center text-white text-xs font-bold ${
                    idx === 0 ? 'bg-sky-400' : idx === 1 ? 'bg-grad-accent' : 'bg-grad-violet'}`}>{idx + 1}</span>
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
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [jobId, setJobId] = useState<number | null>(null)
  const [mode, setMode] = useState<'time' | 'level'>('time')
  const [jds, setJds] = useState<string[]>([SAMPLE_JD])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<any>(null)
  const toast = useToast()
  const floatRef = useFloat<HTMLImageElement>({ y: 9, duration: 3.6, deps: [history, result, loading] })

  useEffect(() => {
    api.jobs({ size: 100, is_new: false })
      .then(d => { setJobs(d.items); const pref = d.items.find(j => j.name === 'Java开发工程师') || d.items[0]; if (pref) setJobId(pref.id) })
      .catch(e => toast('error', errMsg(e, '岗位列表加载失败')))
  }, [])
  useEffect(() => { if (jobId) { api.changes(jobId).then(setHistory).catch(() => setHistory({ items: [] })); setResult(null) } }, [jobId])

  const run = async () => {
    if (!jobId) return
    setLoading(true)
    try {
      const r = await api.evolve(jobId, jds.filter(j => j.trim()), true)
      setResult(r)
      // 只读演示站下后端只推演不落库（dry_run），措辞必须跟着变——否则页面说"演化完成"
      // 而历史记录里什么都没多出来，等于当着评委的面自相矛盾。
      toast('success', r.dry_run
        ? `推演完成（演示站只读，未写入图谱）：新增 ${r.evolution.added} · 删除 ${r.evolution.deleted} · 修改 ${r.evolution.modified}`
        : `演化完成：新增 ${r.evolution.added} · 删除 ${r.evolution.deleted} · 修改 ${r.evolution.modified}`)
      api.changes(jobId).then(setHistory).catch(() => {})
    } catch (e) {
      // 失败保留已输入的 JD，便于修正后重试
      toast('error', errMsg(e, '演化失败，请稍后重试'))
    } finally { setLoading(false) }
  }


  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow">
          <IGitBranch className="w-6 h-6 text-white" />
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
              mode === k ? 'bg-grad-accent text-white shadow-glow' : 'btn-ghost'}`}>
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

      {mode === 'level' ? (jobId ? <LevelEvolution jobId={jobId} /> : <Spinner />) : (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card className="p-5 space-y-3">
          <div className="label">选择待演化岗位</div>
          <Select value={jobId ?? ''} onChange={v => setJobId(Number(v))} label="选择待演化岗位"
            options={jobs.map(j => ({ value: String(j.id), label: `${j.name}（${j.category}）` }))} />
          <div className="flex items-center justify-between">
            <div className="label">输入最新 JD（可多条）</div>
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
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />} 识别能力变化并演化
          </button>
        </Card>

        <Card className="p-5">
          {loading ? <Spinner label="解析新 JD 并交叉验证…" /> : result ? (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                {[['新增', result.evolution.added, 'emerald'], ['删除', result.evolution.deleted, 'rose'], ['修改', result.evolution.modified, 'amber']].map(
                  ([l, v, t]: any) => (
                    <div key={l} className="rounded-xl bg-sky-50/70 p-3 text-center">
                      <div className={`text-2xl font-extrabold ${t === 'emerald' ? 'text-emerald-600' : t === 'rose' ? 'text-rose-600' : 'text-amber-600'}`}>{v}</div>
                      <div className="text-xs text-slate-500">{l}能力项</div>
                    </div>
                  ))}
              </div>
              <div className="space-y-2 max-h-[360px] overflow-auto">
                {result.changes.map((c: any, i: number) => (
                  <div key={i} className="rounded-xl bg-sky-50/70 px-3.5 py-2.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-slate-800">{c.skill_name}</span>
                      <ChangeDiff change={c} />
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{c.reason}</p>
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
                    className="w-40 h-40 object-contain mx-auto mb-2 mix-blend-multiply drop-shadow-[0_12px_24px_rgba(52,211,153,0.2)]" />
                  <div className="text-slate-400">暂无演化记录，提交新 JD 开始演化。</div>
                </div>
              ) : (
                <div className="space-y-2 max-h-[420px] overflow-auto">
                  {history.items.slice(0, 20).map((c: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 text-xs flex-wrap" title={c.reason || ''}>
                      <span className="text-slate-700">{c.skill_name}</span>
                      <ChangeDiff change={c} compact />
                      <span className="text-slate-400">v{c.version}</span>
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
