import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Briefcase, Search, Sparkles, PlusCircle } from 'lucide-react'
import { api, JobListItem, CATEGORY_COLORS } from '../api'
import { Card, Spinner, ConfidencePill, Badge, EmptyState } from '../components/ui'
import Select from '../components/Select'

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' }

export default function Jobs() {
  const [items, setItems] = useState<JobListItem[]>([])
  const [cats, setCats] = useState<string[]>([])
  const [cat, setCat] = useState('全部')
  const [q, setQ] = useState('')
  const [onlyNew, setOnlyNew] = useState(false)
  const [loading, setLoading] = useState(true)
  const nav = useNavigate()

  useEffect(() => { api.categories().then(d => setCats(['全部', ...d.categories])) }, [])
  const load = () => {
    setLoading(true)
    const params: any = { size: 100 }
    if (cat !== '全部') params.category = cat
    if (q) params.q = q
    if (onlyNew) params.is_new = true
    api.jobs(params).then(d => { setItems(d.items); setLoading(false) })
  }
  useEffect(load, [cat, onlyNew])

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow">
          <Briefcase className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">岗位库管理</h1>
          <p className="text-sm text-slate-500">{items.length} 个岗位 · 支持检索与人工优化</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-white/80 border border-slate-200 flex-1 min-w-[220px] focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/15 transition">
          <Search className="w-4 h-4 text-slate-400" />
          <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && load()}
            placeholder="搜索岗位名称…" className="bg-transparent text-sm outline-none flex-1 text-slate-800 placeholder:text-slate-400" />
        </div>
        <Select value={cat} onChange={setCat} options={cats} className="w-44" />
        <button onClick={() => setOnlyNew(v => !v)}
          className={onlyNew ? 'btn-primary' : 'btn-ghost'}>
          <Sparkles className="w-4 h-4" /> 仅新兴岗位
        </button>
        <button onClick={() => nav('/discovery')} className="btn-ghost">
          <PlusCircle className="w-4 h-4" /> 发现新岗位
        </button>
      </div>

      {loading ? <Spinner /> : items.length === 0 ? <EmptyState text="未找到匹配的岗位" /> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {items.map((j, i) => (
            <Card key={j.id} delay={i * 0.02} hover className="p-5 cursor-pointer group"
              >
              <div onClick={() => nav(`/jobs/${j.id}`)}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: CATEGORY_COLORS[j.category] || '#64748B' }} />
                    <h3 className="font-bold text-slate-800 truncate group-hover:text-slate-900">{j.name}</h3>
                  </div>
                  {j.is_new && <Badge tone="amber">新兴</Badge>}
                </div>
                <p className="text-xs text-slate-500 mt-2 line-clamp-2 min-h-[32px]">{j.summary}</p>
                <div className="flex items-center justify-between mt-3">
                  <div className="flex gap-1.5">
                    <Badge tone="indigo">{j.category}</Badge>
                    <Badge tone="slate">{LEVEL_LABEL[j.level] || j.level}</Badge>
                  </div>
                  <ConfidencePill value={j.confidence} />
                </div>
                <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-200/70 text-[11px] text-slate-400">
                  <span>{j.required_count} 项必备技能</span>
                  <span>证据 {j.evidence_count} · v{j.version}</span>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
