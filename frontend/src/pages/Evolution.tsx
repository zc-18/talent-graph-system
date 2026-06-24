import { useEffect, useState } from 'react'
import { GitBranch, Loader2, Plus, X, Wand2, ArrowRight } from 'lucide-react'
import { api, JobListItem } from '../api'
import { Card, Badge, Spinner } from '../components/ui'
import Select from '../components/Select'

const SAMPLE_JD = `招聘高级Java开发工程师
岗位职责：负责核心交易系统研发，参与AI能力中台建设。
任职要求：
1. 精通Java、Spring Cloud微服务；
2. 熟悉分布式系统、高并发、消息队列；
3. 熟练使用 Kubernetes、Docker 容器化部署；
4. 了解大语言模型应用开发、RAG检索增强、向量数据库者优先；
5. 有云原生、Service Mesh 经验加分。`

export default function Evolution() {
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [jobId, setJobId] = useState<number | null>(null)
  const [jds, setJds] = useState<string[]>([SAMPLE_JD])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<any>(null)

  useEffect(() => { api.jobs({ size: 100, is_new: false }).then(d => { setJobs(d.items); if (d.items[0]) setJobId(d.items[0].id) }) }, [])
  useEffect(() => { if (jobId) { api.changes(jobId).then(setHistory); setResult(null) } }, [jobId])

  const run = async () => {
    if (!jobId) return
    setLoading(true)
    try {
      const r = await api.evolve(jobId, jds.filter(j => j.trim()), true)
      setResult(r)
      api.changes(jobId).then(setHistory)
    } finally { setLoading(false) }
  }

  const TYPE = { add: ['新增', 'emerald'], delete: ['删除', 'rose'], modify: ['修改', 'amber'] } as any

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow">
          <GitBranch className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">既有岗位能力动态演化</h1>
          <p className="text-sm text-slate-500">用最新招聘 JD 驱动既有岗位能力更新，自动标注新增 / 删除 / 修改并溯源</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card className="p-5 space-y-3">
          <div className="label">选择待演化岗位</div>
          <Select value={jobId ?? ''} onChange={v => setJobId(Number(v))}
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
                <button onClick={() => setJds(jds.filter((_, k) => k !== i))}
                  className="absolute top-2 right-2 text-slate-400 hover:text-rose-400"><X className="w-4 h-4" /></button>
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
                {result.changes.map((c: any, i: number) => {
                  const [label, tone] = TYPE[c.change_type]
                  return (
                    <div key={i} className="rounded-xl bg-sky-50/70 px-3.5 py-2.5">
                      <div className="flex items-center gap-2">
                        <Badge tone={tone}>{label}</Badge>
                        <span className="text-sm font-semibold text-slate-800">{c.skill_name}</span>
                        {c.change_type === 'modify' && c.old_value && c.new_value && (
                          <span className="text-[11px] text-slate-400 flex items-center gap-1">
                            {JSON.stringify(c.old_value.importance || c.old_value.weight)} <ArrowRight className="w-3 h-3" /> {JSON.stringify(c.new_value.importance || c.new_value.weight)}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-500 mt-1">{c.reason}</p>
                    </div>
                  )
                })}
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500">
              <div className="label mb-3">该岗位历史演化记录</div>
              {!history ? <Spinner /> : history.items.length === 0 ? (
                <div className="text-slate-400 text-center py-10">暂无演化记录，提交新 JD 开始演化。</div>
              ) : (
                <div className="space-y-2 max-h-[420px] overflow-auto">
                  {history.items.slice(0, 20).map((c: any, i: number) => {
                    const [label, tone] = TYPE[c.change_type] || ['变更', 'slate']
                    return (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <Badge tone={tone}>{label}</Badge>
                        <span className="text-slate-700">{c.skill_name}</span>
                        <span className="text-slate-400">v{c.version}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
