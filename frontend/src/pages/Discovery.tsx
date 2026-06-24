import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles, Search, Globe, Save, Zap, Loader2, ExternalLink } from 'lucide-react'
import { api } from '../api'
import { Card, Badge, ConfidencePill } from '../components/ui'

export default function Discovery() {
  const [seeds, setSeeds] = useState<string[]>([])
  const [kw, setKw] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [res, setRes] = useState<any>(null)
  const nav = useNavigate()

  useEffect(() => { api.seeds().then(d => setSeeds(d.seeds)) }, [])

  const run = async (keyword: string, save = false) => {
    if (!keyword.trim()) return
    save ? setSaving(true) : setLoading(true)
    try {
      const r = await api.discover(keyword, save)
      setRes(r)
      if (save && r.saved) nav(`/jobs/${r.saved.id}`)
    } finally { setLoading(false); setSaving(false) }
  }

  const def = res?.definition
  const cand = res?.candidate

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow animate-float">
          <Sparkles className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">新岗位发现与定义</h1>
          <p className="text-sm text-slate-500">多源联网检索（Tavily + Serper）+ 大模型 RAG 接地，识别萌芽中的新兴岗位</p>
        </div>
      </div>

      <Card className="p-5">
        <div className="flex gap-2">
          <div className="glass flex items-center gap-2 px-3 py-2.5 flex-1">
            <Search className="w-4 h-4 text-slate-500" />
            <input value={kw} onChange={e => setKw(e.target.value)} onKeyDown={e => e.key === 'Enter' && run(kw)}
              placeholder="输入新兴岗位关键词，如：提示词工程师 / AI智能体开发工程师"
              className="bg-transparent text-sm outline-none flex-1 text-slate-800 placeholder:text-slate-400" />
          </div>
          <button onClick={() => run(kw)} disabled={loading} className="btn-primary">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />} 发现并定义
          </button>
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          {seeds.map(s => (
            <button key={s} onClick={() => { setKw(s); run(s) }}
              className="chip border bg-white/70 border-slate-200 text-slate-600 hover:border-accent/50 hover:text-slate-700 transition">
              {s}
            </button>
          ))}
        </div>
      </Card>

      {loading && (
        <Card className="p-10 text-center">
          <Loader2 className="w-8 h-8 animate-spin mx-auto text-accent" />
          <p className="text-sm text-slate-500 mt-3">正在多源检索证据并生成岗位定义…</p>
        </Card>
      )}

      {!def && !loading && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <Card className="lg:col-span-2 p-7" delay={0.05}>
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="w-5 h-5 text-violet-500" />
              <h3 className="text-lg font-bold text-slate-800">从一个关键词，发现一个新职业</h3>
            </div>
            <p className="text-sm text-slate-500 mb-5">系统不是凭空想象，而是先检索真实网络证据，再由大模型基于证据生成可溯源的岗位定义。</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {[
                [Globe, '多源检索证据', 'Tavily + Serper 双独立来源并行检索，统计独立来源数', '#0EA5E9'],
                [Zap, '大模型 RAG 接地', '基于证据生成岗位定义，约束"不臆造证据外内容"', '#6366F1'],
                [Save, '交叉验证入库', '逐项校验证据、评估置信度，沉淀进全景图谱', '#10B981'],
              ].map(([Icon, t, d, c]: any, i: number) => (
                <div key={i} className="rounded-2xl bg-sky-50/70 border border-slate-200/60 p-4">
                  <div className="w-9 h-9 rounded-xl grid place-items-center mb-2.5" style={{ background: c }}>
                    <Icon className="w-5 h-5 text-white" />
                  </div>
                  <div className="text-[13px] font-bold text-slate-800">{t}</div>
                  <div className="text-[11px] text-slate-500 mt-1 leading-relaxed">{d}</div>
                </div>
              ))}
            </div>
            <div className="mt-5 text-xs text-slate-400">💡 点击上方任一推荐岗位，或输入关键词，即可开始发现。</div>
          </Card>
          <Card className="p-6 flex flex-col items-center justify-center text-center" delay={0.1}>
            <div className="w-20 h-20 rounded-full bg-grad-violet grid place-items-center shadow-glow mb-4 animate-float">
              <Sparkles className="w-10 h-10 text-white" />
            </div>
            <div className="text-sm font-semibold text-slate-700">已内置 {seeds.length} 个新兴岗位候选</div>
            <div className="text-xs text-slate-400 mt-1.5 leading-relaxed">覆盖大模型应用、AI 智能体、RAG、具身智能、MLOps 等前沿方向</div>
          </Card>
        </div>
      )}

      {def && !loading && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 space-y-5">
            <Card className="p-6">
              <div className="flex items-start justify-between">
                <div>
                  <Badge tone="amber">新兴岗位</Badge>
                  <h2 className="text-2xl font-extrabold text-slate-900 mt-2">{def.job_title}</h2>
                  <div className="flex gap-2 mt-2">
                    <Badge tone="indigo">{def.category}</Badge>
                    <Badge tone="cyan">新兴度 {Math.round((def.emergence_score || 0) * 100)}%</Badge>
                  </div>
                </div>
                <button onClick={() => run(def.job_title, true)} disabled={saving} className="btn-primary">
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} 保存到图谱
                </button>
              </div>
              <p className="text-sm text-slate-600 mt-3 leading-relaxed">{def.summary}</p>

              <div className="label mt-5 mb-2">核心职责</div>
              <ul className="space-y-1.5">
                {def.core_responsibilities.map((r: string, i: number) => (
                  <li key={i} className="text-sm text-slate-600 flex gap-2"><span className="text-accent font-bold">{i + 1}</span>{r}</li>
                ))}
              </ul>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 mt-5">
                <div>
                  <div className="label mb-2">必备技能</div>
                  <div className="space-y-1.5">
                    {def.capabilities.filter((c: any) => c.importance === 'required').map((c: any, i: number) => (
                      <div key={i} className="flex items-center justify-between bg-sky-50/70 rounded-lg px-3 py-1.5">
                        <span className="text-sm text-slate-800">{c.name}</span>
                        <div className="flex items-center gap-1.5">
                          {c.web_verified && <Badge tone="emerald">已验证</Badge>}
                          <ConfidencePill value={c.confidence} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="label mb-2">加分技能</div>
                  <div className="flex flex-wrap gap-1.5">
                    {def.capabilities.filter((c: any) => c.importance === 'bonus').map((c: any, i: number) => (
                      <Badge key={i} tone="slate">{c.name}</Badge>
                    ))}
                  </div>
                  <div className="label mt-4 mb-2">典型应用场景</div>
                  <div className="flex flex-wrap gap-1.5">
                    {def.typical_scenarios.map((s: string, i: number) => <Badge key={i} tone="cyan">{s}</Badge>)}
                  </div>
                </div>
              </div>
            </Card>
          </div>

          <Card className="p-5">
            <div className="flex items-center gap-2 label mb-3"><Globe className="w-4 h-4 text-cyan-600" /> 多源证据 ({cand?.evidence_count})</div>
            <div className="flex gap-2 mb-3">
              <Badge tone="cyan">独立来源 {cand?.independent_sources}</Badge>
              <Badge tone="emerald">交叉验证</Badge>
            </div>
            <div className="space-y-2 max-h-[520px] overflow-auto pr-1">
              {cand?.evidence?.map((e: any, i: number) => (
                <a key={i} href={e.url} target="_blank" rel="noreferrer"
                  className="block rounded-xl bg-sky-50/70 hover:bg-sky-100/80 px-3 py-2.5 transition group">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-700 truncate">{e.title || '证据来源'}</span>
                    <ExternalLink className="w-3 h-3 text-slate-400 shrink-0" />
                  </div>
                  <p className="text-[11px] text-slate-400 mt-1 line-clamp-2">{e.content}</p>
                  {e.provider && <span className="text-[10px] text-accent">{e.provider}</span>}
                </a>
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
