import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, ShieldCheck, FileText, History, Plus, Trash2, Pencil, Target, Layers, Briefcase,
} from 'lucide-react'
import { api, JobDetail as TJob, CATEGORY_COLORS } from '../api'
import { Card, Spinner, ConfidencePill, Badge } from '../components/ui'

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' }
const SKILL_LEVEL: Record<string, string> = { familiar: '了解', proficient: '熟练', expert: '精通' }

function SkillRow({ s, onEdit, onRemove }: any) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-sky-50/70 hover:bg-sky-100/80 px-3.5 py-2.5 transition group">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-800">{s.name}</span>
          <Badge tone="slate">{s.category}</Badge>
          <span className="text-[11px] text-slate-400">{SKILL_LEVEL[s.level_required] || ''}</span>
        </div>
        <div className="mt-1.5 h-1.5 rounded-full bg-sky-50/80 overflow-hidden">
          <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.round(s.weight * 100)}%` }} />
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-[11px] text-slate-400" title="独立来源数">×{s.source_count}</span>
        <ConfidencePill value={s.confidence} />
        <button onClick={() => onEdit(s)} className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-accent transition"><Pencil className="w-3.5 h-3.5" /></button>
        <button onClick={() => onRemove(s)} className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-rose-400 transition"><Trash2 className="w-3.5 h-3.5" /></button>
      </div>
    </div>
  )
}

export default function JobDetail() {
  const { id } = useParams()
  const jobId = Number(id)
  const nav = useNavigate()
  const [job, setJob] = useState<TJob | null>(null)
  const [tab, setTab] = useState<'profile' | 'evidence' | 'history'>('profile')
  const [evidence, setEvidence] = useState<any>(null)
  const [history, setHistory] = useState<any>(null)
  const [editor, setEditor] = useState<any>(null)

  const reload = () => api.job(jobId).then(setJob)
  useEffect(() => { reload() }, [jobId])
  useEffect(() => {
    if (tab === 'evidence' && !evidence) api.jobEvidence(jobId).then(setEvidence)
    if (tab === 'history' && !history) api.changes(jobId).then(setHistory)
  }, [tab])

  if (!job) return <Spinner />
  const color = CATEGORY_COLORS[job.category] || '#6366F1'

  const saveEdit = async (action: string, payload: any) => {
    await api.manualEdit({ job_id: jobId, action, ...payload })
    setEditor(null); setEvidence(null); setHistory(null); reload()
  }

  return (
    <div className="space-y-5">
      <button onClick={() => nav(-1)} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700">
        <ArrowLeft className="w-4 h-4" /> 返回
      </button>

      <Card className="p-6 relative overflow-hidden">
        <div className="absolute -top-16 -right-10 w-56 h-56 rounded-full blur-3xl opacity-25" style={{ background: color }} />
        <div className="flex items-start justify-between gap-4 relative">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Badge tone="indigo">{job.category}</Badge>
              <Badge tone="slate">{LEVEL_LABEL[job.level] || job.level}</Badge>
              {job.is_new && <Badge tone="amber">新兴岗位 · 新兴度 {Math.round(job.emergence_score * 100)}%</Badge>}
              <Badge tone="cyan">v{job.version}</Badge>
            </div>
            <h1 className="text-3xl font-extrabold text-slate-900">{job.name}</h1>
            <p className="text-sm text-slate-500 mt-2 max-w-3xl leading-relaxed">{job.summary}</p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-xs text-slate-500 mb-1">岗位定义置信度</div>
            <div className="text-3xl font-extrabold gradient-text">{Math.round(job.confidence * 100)}%</div>
            <div className="text-[11px] text-slate-400 mt-1">{job.evidence_count} 条证据支撑</div>
            <button onClick={() => nav('/match', { state: { jobId } })} className="btn-primary mt-3">
              <Target className="w-4 h-4" /> 匹配此岗位
            </button>
          </div>
        </div>
      </Card>

      <div className="flex gap-1.5">
        {[['profile', '能力画像', Layers], ['evidence', '溯源证据', ShieldCheck], ['history', '演化历史', History]].map(
          ([k, label, Icon]: any) => (
            <button key={k} onClick={() => setTab(k)}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition ${
                tab === k ? 'bg-grad-accent text-white shadow-glow' : 'btn-ghost'}`}>
              <Icon className="w-4 h-4" /> {label}
            </button>
          ))}
        <button onClick={() => setEditor({ action: 'add', skill_name: '', importance: 'required', weight: 0.6, level_required: 'familiar' })}
          className="btn-ghost ml-auto"><Plus className="w-4 h-4" /> 人工新增能力项</button>
      </div>

      {tab === 'profile' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 space-y-5">
            <Card className="p-5">
              <div className="label mb-3 flex items-center gap-2"><Target className="w-4 h-4 text-accent" /> 必备技能 ({job.required_skills.length})</div>
              <div className="space-y-2">
                {job.required_skills.map(s => (
                  <SkillRow key={s.skill_id} s={s}
                    onEdit={(sk: any) => setEditor({ action: 'update', skill_name: sk.name, importance: sk.importance, weight: sk.weight, level_required: sk.level_required })}
                    onRemove={(sk: any) => saveEdit('remove', { skill_name: sk.name })} />
                ))}
              </div>
            </Card>
            {job.bonus_skills.length > 0 && (
              <Card className="p-5">
                <div className="label mb-3">加分技能 ({job.bonus_skills.length})</div>
                <div className="flex flex-wrap gap-2">
                  {job.bonus_skills.map(s => (
                    <span key={s.skill_id} className="chip border bg-white/70 border-slate-200 text-slate-600">
                      {s.name} <span className="text-slate-400">·{Math.round(s.confidence * 100)}%</span>
                    </span>
                  ))}
                </div>
              </Card>
            )}
          </div>
          <div className="space-y-5">
            <Card className="p-5">
              <div className="label mb-3 flex items-center gap-2"><Briefcase className="w-4 h-4 text-violet-600" /> 核心职责</div>
              <ul className="space-y-2">
                {job.core_responsibilities.map((r, i) => (
                  <li key={i} className="text-sm text-slate-600 flex gap-2">
                    <span className="text-accent font-bold">{i + 1}</span>{r}
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="p-5">
              <div className="label mb-3">典型行业应用场景</div>
              <div className="flex flex-wrap gap-2">
                {job.typical_scenarios.map((s, i) => <Badge key={i} tone="cyan">{s}</Badge>)}
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === 'evidence' && (
        <Card className="p-5">
          <div className="text-sm text-slate-500 mb-4 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            反幻觉机制：每个能力项均保留多源证据与置信度，可追溯到原始招聘 JD
          </div>
          {!evidence ? <Spinner /> : (
            <div className="space-y-2">
              {evidence.items.map((it: any, i: number) => (
                <details key={i} className="rounded-xl bg-sky-50/70 px-4 py-3 group">
                  <summary className="flex items-center justify-between cursor-pointer list-none">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-slate-500" />
                      <span className="text-sm font-medium text-slate-800">{it.skill}</span>
                      <Badge tone={it.importance === 'required' ? 'indigo' : 'slate'}>
                        {it.importance === 'required' ? '必备' : '加分'}</Badge>
                      {it.status === 'deprecated' && <Badge tone="rose">已淘汰</Badge>}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      {it.source_count} 来源 · <ConfidencePill value={it.confidence} />
                    </div>
                  </summary>
                  <div className="mt-2 pl-6 space-y-1.5">
                    {it.evidences.slice(0, 6).map((e: any, j: number) => (
                      <div key={j} className="text-xs text-slate-500 flex gap-2">
                        <Badge tone={e.type === 'web' ? 'cyan' : 'slate'}>{e.type}</Badge>
                        <span className="truncate">{e.snippet}</span>
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          )}
        </Card>
      )}

      {tab === 'history' && (
        <Card className="p-5">
          {!history ? <Spinner /> : history.items.length === 0 ? (
            <div className="text-center py-12 text-slate-400 text-sm">
              暂无演化记录。可在「岗位能力演化」页用新 JD 驱动该岗位能力更新。
            </div>
          ) : (
            <div className="relative pl-6">
              <div className="absolute left-2 top-1 bottom-1 w-px bg-white/10" />
              {history.items.map((c: any, i: number) => {
                const tone = c.change_type === 'add' ? 'emerald' : c.change_type === 'delete' ? 'rose' : 'amber'
                const label = { add: '新增', delete: '删除', modify: '修改' }[c.change_type] || c.change_type
                return (
                  <div key={i} className="relative pb-5">
                    <span className={`absolute -left-[18px] top-1 w-3 h-3 rounded-full ring-4 ring-white ${
                      tone === 'emerald' ? 'bg-emerald-400' : tone === 'rose' ? 'bg-rose-400' : 'bg-amber-400'}`} />
                    <div className="flex items-center gap-2">
                      <Badge tone={tone}>{label}</Badge>
                      <span className="text-sm font-semibold text-slate-800">{c.skill_name}</span>
                      <span className="text-[11px] text-slate-400">v{c.version}</span>
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{c.reason}</p>
                  </div>
                )
              })}
            </div>
          )}
        </Card>
      )}

      {editor && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 backdrop-blur-sm" onClick={() => setEditor(null)}>
          <div className="glass p-6 w-[420px]" onClick={e => e.stopPropagation()}>
            <div className="text-lg font-bold text-slate-900 mb-4">{editor.action === 'add' ? '新增能力项' : '编辑能力项'}</div>
            <div className="space-y-3">
              <div>
                <div className="label mb-1">技能名称</div>
                <input className="input" value={editor.skill_name}
                  onChange={e => setEditor({ ...editor, skill_name: e.target.value })} placeholder="如：检索增强生成" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="label mb-1">重要度</div>
                  <select className="input" value={editor.importance}
                    onChange={e => setEditor({ ...editor, importance: e.target.value })}>
                    <option value="required" className="bg-white">必备</option>
                    <option value="bonus" className="bg-white">加分</option>
                  </select>
                </div>
                <div>
                  <div className="label mb-1">掌握级别</div>
                  <select className="input" value={editor.level_required}
                    onChange={e => setEditor({ ...editor, level_required: e.target.value })}>
                    <option value="familiar" className="bg-white">了解</option>
                    <option value="proficient" className="bg-white">熟练</option>
                    <option value="expert" className="bg-white">精通</option>
                  </select>
                </div>
              </div>
              <div>
                <div className="label mb-1">权重 {Math.round(editor.weight * 100)}%</div>
                <input type="range" min={0.1} max={1} step={0.05} value={editor.weight}
                  onChange={e => setEditor({ ...editor, weight: parseFloat(e.target.value) })}
                  className="w-full accent-accent" />
              </div>
            </div>
            <div className="flex gap-2 mt-5">
              <button className="btn-ghost flex-1" onClick={() => setEditor(null)}>取消</button>
              <button className="btn-primary flex-1" disabled={!editor.skill_name}
                onClick={() => saveEdit(editor.action, {
                  skill_name: editor.skill_name, importance: editor.importance,
                  weight: editor.weight, level_required: editor.level_required })}>
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
