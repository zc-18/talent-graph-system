import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, FileText, History, Plus, Trash2, Pencil, ExternalLink, Landmark, Sparkles, ChevronRight,
} from 'lucide-react'
import { ITarget, IStack, IShieldCheck, IBriefcase } from '../components/icons'
import { api, errMsg, JobDetail as TJob, Skill as TSkill, AuthorityItem, CATEGORY_COLORS } from '../api'
import { Card, Spinner, ConfidencePill, Badge, ErrorState } from '../components/ui'
import ChangeDiff from '../components/ChangeDiff'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { useReveal } from '../hooks/gsapFx'

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' }
const SKILL_LEVEL: Record<string, string> = { familiar: '了解', proficient: '熟练', expert: '精通' }

function FineChips({ items }: { items: TSkill[] }) {
  return (
    <>
      {items.map((f: TSkill) => (
        <span key={f.skill_id} className="chip border bg-white/80 border-sky-200 text-slate-600 text-[11px]"
          title={`置信度 ${Math.round(f.confidence * 100)}%`}>
          {f.name} <span className="text-slate-400">·{Math.round(f.confidence * 100)}%</span>
        </span>
      ))}
    </>
  )
}

/** 候选技能点：单来源、未通过交叉验证，默认折叠。
 *  题目要求颗粒度到「技能点」级别，所以不能删；但把上百个未验证碎片和已确认能力
 *  平铺在一起，页面会拉到一万多像素，且看起来像「这个岗位有 880 项能力要求」。
 *  折叠后既保留可展开的技能点颗粒度，也让「已确认 vs 待验证」的分界自己说话。 */
function CandidateChips({ items }: { items: TSkill[] }) {
  const [open, setOpen] = useState(false)
  if (!items.length) return null
  return (
    <div className="mt-2 pt-2 border-t border-slate-100">
      <button onClick={() => setOpen(o => !o)}
        className="text-[11px] text-slate-400 hover:text-slate-600 transition inline-flex items-center gap-1">
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`} />
        候选技能点 {items.length} 项（单来源，待交叉验证）
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {items.map((f: TSkill) => (
            <span key={f.skill_id} className="chip border border-dashed bg-slate-50/80 border-slate-300 text-slate-500 text-[11px]"
              title={`置信度 ${Math.round(f.confidence * 100)}% · 仅 ${f.source_count} 个来源`}>
              {f.name} <span className="text-slate-400">·{Math.round(f.confidence * 100)}%</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SkillRow({ s, fineChildren = [], fineCandidates = [], onEdit, onRemove }: any) {
  return (
    <div className="rounded-xl bg-sky-50/70 hover:bg-sky-100/80 px-3.5 py-2.5 transition group">
      {/* 首行：技能名 + 分类/级别；操作按钮固定右侧。徽章不换行，窄屏截断而非竖排 */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-medium text-slate-800 shrink-0">{s.name}</span>
        <span className="chip border bg-slate-100 text-slate-600 border-slate-200 whitespace-nowrap truncate min-w-0">{s.category}</span>
        <span className="text-[11px] text-slate-400 shrink-0 hidden sm:inline">{SKILL_LEVEL[s.level_required] || ''}</span>
        <span className="flex-1" />
        <button onClick={() => onEdit(s)} aria-label={`编辑技能 ${s.name}`}
          className="opacity-100 lg:opacity-0 lg:group-hover:opacity-100 text-slate-500 hover:text-accent transition p-2 -m-1 shrink-0 rounded-lg focus-visible:ring-2 focus-visible:ring-accent/40 outline-none">
          <Pencil className="w-3.5 h-3.5" /></button>
        <button onClick={() => onRemove(s)} aria-label={`删除技能 ${s.name}`}
          className="opacity-100 lg:opacity-0 lg:group-hover:opacity-100 text-slate-500 hover:text-rose-400 transition p-2 -m-1 shrink-0 rounded-lg focus-visible:ring-2 focus-visible:ring-rose-300 outline-none">
          <Trash2 className="w-3.5 h-3.5" /></button>
      </div>
      {/* 次行：权重条 + 来源数/置信度 */}
      <div className="mt-1.5 flex items-center gap-2.5">
        <div className="flex-1 h-1.5 rounded-full bg-sky-50/80 overflow-hidden">
          <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.round(s.weight * 100)}%` }} />
        </div>
        <span className="text-[11px] text-slate-400 shrink-0 sm:hidden">{SKILL_LEVEL[s.level_required] || ''}</span>
        <span className="text-[11px] text-slate-400 shrink-0" title="独立来源数">×{s.source_count}</span>
        <span className="shrink-0"><ConfidencePill value={s.confidence} factors={s.factors} /></span>
      </div>
      {/* 细分技能点：已通过交叉验证的挂父项下直接展示 */}
      {fineChildren.length > 0 && (
        <div className="mt-2 pt-2 border-t border-sky-100/80 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] text-slate-400 shrink-0">细分技能点</span>
          <FineChips items={fineChildren} />
        </div>
      )}
      <CandidateChips items={fineCandidates} />
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
  const [removing, setRemoving] = useState<any>(null)
  const [loadError, setLoadError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [authority, setAuthority] = useState<AuthorityItem[]>([])
  const toast = useToast()
  const revealRef = useReveal('[data-reveal]', { scroll: true, stagger: 0.05, deps: [job, tab] })

  const reload = () => { setLoadError(false); api.job(jobId).then(setJob).catch(() => setLoadError(true)) }
  useEffect(() => { reload() }, [jobId])
  useEffect(() => {
    setAuthority([])
    api.jobAuthority(jobId).then(d => setAuthority(d.items || [])).catch(() => {})
  }, [jobId])
  useEffect(() => {
    if (tab === 'evidence' && !evidence) api.jobEvidence(jobId).then(setEvidence).catch(e => toast('error', errMsg(e, '证据加载失败')))
    if (tab === 'history' && !history) api.changes(jobId).then(setHistory).catch(e => toast('error', errMsg(e, '演化记录加载失败')))
  }, [tab])

  if (loadError) return <ErrorState text="岗位详情加载失败" onRetry={reload} />
  if (!job) return <Spinner />
  const color = CATEGORY_COLORS[job.category] || '#6366F1'
  // 粗/细粒度分组：细粒度技能点作为「细分技能点」挂在其父（粗粒度）技能行下。
  // 再按 status 分流——active 是通过 ≥2 独立来源交叉验证的，直接展示；candidate 是
  // 单来源待验证的，折叠收起（后端 job_to_dict 不按 status 过滤，两类都会返回）。
  const allSkills: TSkill[] = [...job.required_skills, ...job.bonus_skills]
  const isCandidate = (s: TSkill) => (s as any).status === 'candidate'
  const fineByParent = new Map<string, TSkill[]>()
  const candByParent = new Map<string, TSkill[]>()
  for (const s of allSkills) {
    if (s.granularity === 'fine' && s.parent_name) {
      const m = isCandidate(s) ? candByParent : fineByParent
      const arr = m.get(s.parent_name) || []
      arr.push(s); m.set(s.parent_name, arr)
    }
  }
  const coarseRequired = job.required_skills.filter(s => s.granularity !== 'fine' && !isCandidate(s))
  const coarseBonus = job.bonus_skills.filter(s => s.granularity !== 'fine' && !isCandidate(s))
  const isOrphan = (s: TSkill) => s.granularity === 'fine' &&
    (!s.parent_name || !allSkills.some(p => p.granularity !== 'fine' && p.name === s.parent_name))
  const orphanFine = allSkills.filter(s => isOrphan(s) && !isCandidate(s))
  const orphanCand = allSkills.filter(s => isOrphan(s) && isCandidate(s))
  const emergence = (job as any).emergence_type as string | null

  const saveEdit = async (action: string, payload: any) => {
    setSaving(true)
    try {
      await api.manualEdit({ job_id: jobId, action, ...payload })
      toast('success', action === 'remove' ? '能力项已删除' : action === 'add' ? '能力项已新增' : '能力项已更新')
      setEditor(null); setEvidence(null); setHistory(null); reload()
    } catch (e) {
      // 失败保留编辑器内容，便于修正后重试
      toast('error', errMsg(e, '保存失败，请重试'))
    } finally { setSaving(false) }
  }

  return (
    <div ref={revealRef} className="space-y-5">
      <button onClick={() => nav(-1)} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700">
        <ArrowLeft className="w-4 h-4" /> 返回
      </button>

      <Card className="p-6 relative overflow-hidden">
        <div aria-hidden className="absolute inset-0 pointer-events-none bg-cover bg-right opacity-60"
          style={{ backgroundImage: 'url(/hero-jobdetail.webp)' }} />
        <div aria-hidden className="absolute inset-0 pointer-events-none bg-gradient-to-l from-white/75 via-white/20 to-transparent" />
        <div className="absolute -top-16 -right-10 w-56 h-56 rounded-full blur-3xl opacity-25" style={{ background: color }} />
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 relative">
          <div className="min-w-0">
            <div className="flex items-center flex-wrap gap-2 mb-2">
              <Badge tone="indigo">{job.category}</Badge>
              <Badge tone="slate">{LEVEL_LABEL[job.level] || job.level}</Badge>
              {job.is_new && <Badge tone="amber">新兴岗位 · 新兴度 {Math.round(job.emergence_score * 100)}%</Badge>}
              {emergence === 'new' && <Badge tone="rose"><Sparkles className="w-3 h-3 inline -mt-0.5 mr-0.5" />新出现</Badge>}
              {emergence === 'revived' && <Badge tone="emerald"><Sparkles className="w-3 h-3 inline -mt-0.5 mr-0.5" />复兴</Badge>}
              <Badge tone="cyan">v{job.version}</Badge>
            </div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">{job.name}</h1>
            <p className="text-sm text-slate-500 mt-2 max-w-3xl leading-relaxed">{job.summary}</p>
          </div>
          <div className="text-left sm:text-right sm:shrink-0">
            <div className="text-xs text-slate-500 mb-1">岗位定义置信度</div>
            <div className="text-3xl font-extrabold gradient-text">{Math.round(job.confidence * 100)}%</div>
            <div className="text-[11px] text-slate-400 mt-1">{job.evidence_count} 条证据支撑</div>
            <button onClick={() => nav('/match', { state: { jobId } })} className="btn-primary mt-3 w-full sm:w-auto justify-center">
              <ITarget className="w-4 h-4" /> 匹配此岗位
            </button>
          </div>
        </div>
      </Card>

      <div className="flex flex-wrap gap-1.5">
        {[['profile', '能力画像', IStack], ['evidence', '溯源证据', IShieldCheck], ['history', '演化历史', History]].map(
          ([k, label, Icon]: any) => (
            <button key={k} onClick={() => setTab(k)}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium whitespace-nowrap transition ${
                tab === k ? 'bg-grad-accent text-white shadow-glow' : 'btn-ghost'}`}>
              <Icon className="w-4 h-4" /> {label}
            </button>
          ))}
        <button onClick={() => setEditor({ action: 'add', skill_name: '', importance: 'required', weight: 0.6, level_required: 'familiar' })}
          className="btn-ghost w-full sm:w-auto sm:ml-auto justify-center whitespace-nowrap"><Plus className="w-4 h-4" /> 人工新增能力项</button>
      </div>

      {tab === 'profile' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 space-y-5">
            <Card className="p-5">
              <div className="label mb-3 flex items-center gap-2"><ITarget className="w-4 h-4 text-accent" /> 必备技能 ({coarseRequired.length})</div>
              <div className="space-y-2">
                {coarseRequired.map(s => (
                  <div key={s.skill_id} data-reveal>
                  <SkillRow s={s} fineChildren={fineByParent.get(s.name) || []}
                    fineCandidates={candByParent.get(s.name) || []}
                    onEdit={(sk: any) => setEditor({ action: 'update', skill_name: sk.name, importance: sk.importance, weight: sk.weight, level_required: sk.level_required })}
                    onRemove={(sk: any) => setRemoving(sk)} />
                  </div>
                ))}
              </div>
              {(orphanFine.length > 0 || orphanCand.length > 0) && (
                <div className="mt-3 pt-3 border-t border-slate-100">
                  {orphanFine.length > 0 && (
                    <>
                      <div className="text-[11px] text-slate-400 mb-1.5">其他细分技能点</div>
                      <div className="flex flex-wrap gap-1.5"><FineChips items={orphanFine} /></div>
                    </>
                  )}
                  <CandidateChips items={orphanCand} />
                </div>
              )}
            </Card>
            {coarseBonus.length > 0 && (
              <Card className="p-5">
                <div className="label mb-3">加分技能 ({coarseBonus.length})</div>
                <div className="flex flex-wrap gap-2">
                  {coarseBonus.map(s => (
                    <span key={s.skill_id} className="chip border bg-white/70 border-slate-200 text-slate-600">
                      {s.name} <span className="text-slate-400">·{Math.round(s.confidence * 100)}%</span>
                    </span>
                  ))}
                </div>
              </Card>
            )}
          </div>
          {/* 右列高度通常小于左列：sticky 跟随滚动，避免滚到底部时右侧大片留白 */}
          <div className="space-y-5 lg:sticky lg:top-6 self-start">
            <Card className="p-5">
              <div className="label mb-3 flex items-center gap-2"><IBriefcase className="w-4 h-4 text-violet-600" /> 核心职责</div>
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
            {authority.length > 0 && (
              <Card className="p-5">
                <div className="label mb-3 flex items-center gap-2">
                  <Landmark className="w-4 h-4 text-indigo-600" /> 权威依据
                </div>
                <div className="space-y-2.5">
                  {authority.map((a, i) => (
                    <div key={i} className="rounded-xl bg-sky-50/70 px-3 py-2.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge tone={a.kind === 'policy' ? 'indigo' : 'cyan'}>
                          {a.kind === 'policy' ? '部委文件' : '机构报告'}</Badge>
                        <span className="text-[11px] text-slate-400">{a.issuer}{a.publish_date ? ` · ${String(a.publish_date).slice(0, 10)}` : ''}</span>
                      </div>
                      {a.url ? (
                        <a href={a.url} target="_blank" rel="noreferrer"
                          className="mt-1 flex items-start gap-1 text-sm font-medium text-slate-800 hover:text-accent">
                          <span className="min-w-0">{a.title}</span>
                          <ExternalLink className="w-3 h-3 text-slate-400 shrink-0 mt-1" />
                        </a>
                      ) : (
                        <div className="mt-1 text-sm font-medium text-slate-800">{a.title}</div>
                      )}
                      {a.excerpt && <p className="text-[11px] text-slate-500 mt-1 line-clamp-3">{a.excerpt}</p>}
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        </div>
      )}

      {tab === 'evidence' && (
        <Card className="p-5">
          <div className="text-sm text-slate-500 mb-4 flex items-center gap-2">
            <IShieldCheck className="w-4 h-4 text-emerald-600" />
            反幻觉机制：每个能力项均保留多源证据与置信度，可追溯到原始招聘 JD
          </div>
          {!evidence ? <Spinner /> : (
            <div className="space-y-2">
              {evidence.items.map((it: any, i: number) => {
                const evs = it.evidences || []
                return (
                <details key={i} className="rounded-xl bg-sky-50/70 px-4 py-3 group">
                  <summary className="flex items-center justify-between gap-2 flex-wrap cursor-pointer list-none">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-slate-500" />
                      <span className="text-sm font-medium text-slate-800">{it.skill}</span>
                      <Badge tone={it.importance === 'required' ? 'indigo' : 'slate'}>
                        {it.importance === 'required' ? '必备' : '加分'}</Badge>
                      {it.status === 'deprecated' && <Badge tone="rose">已淘汰</Badge>}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      {evs.length > 0 ? `${it.source_count} 来源 · ` : ''}<ConfidencePill value={it.confidence} factors={it.factors} />
                    </div>
                  </summary>
                  {evs.length === 0 ? (
                    <div className="mt-2 pl-6 text-xs text-slate-400">该能力项暂无独立JD证据（人工添加或低频项）</div>
                  ) : (
                  <div className="mt-2.5 pl-0 sm:pl-6 grid grid-cols-1 md:grid-cols-2 gap-2">
                    {evs.slice(0, 8).map((e: any, j: number) => {
                      const inner = (
                        <>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <Badge tone={e.type === 'web' ? 'cyan' : e.type === 'llm' ? 'amber' : 'indigo'}>
                              {e.type === 'web' ? '网络佐证' : e.type === 'llm' ? 'LLM' : e.source || '招聘JD'}</Badge>
                            {e.type === 'web' && e.source && <span className="text-[10px] text-accent">{e.source}</span>}
                            {e.company && <span className="text-[11px] text-slate-500 truncate">{e.company}</span>}
                            {e.job_title && <span className="text-[11px] text-slate-400 truncate hidden sm:inline">{e.job_title}</span>}
                            <span className="flex-1" />
                            {e.publish_date && <span className="text-[10px] text-slate-400 shrink-0">{String(e.publish_date).slice(0, 10)}</span>}
                            {e.url && <ExternalLink className="w-3 h-3 text-slate-400 shrink-0" />}
                          </div>
                          <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{e.snippet}</p>
                        </>
                      )
                      const cls = 'block rounded-lg bg-white/80 border border-sky-100 px-3 py-2 transition'
                      return e.url ? (
                        <a key={j} href={e.url} target="_blank" rel="noreferrer" className={`${cls} hover:bg-sky-100/70 hover:border-sky-200`}>{inner}</a>
                      ) : (
                        <div key={j} className={cls}>{inner}</div>
                      )
                    })}
                  </div>
                  )}
                </details>
                )
              })}
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
              <div className="absolute left-2 top-1 bottom-1 w-px bg-slate-200" />
              {history.items.map((c: any, i: number) => {
                const tone = c.change_type === 'add' ? 'emerald' : c.change_type === 'delete' ? 'rose' : 'amber'
                return (
                  <div key={i} className="relative pb-5">
                    <span className={`absolute -left-[18px] top-1 w-3 h-3 rounded-full ring-4 ring-white ${
                      tone === 'emerald' ? 'bg-emerald-400' : tone === 'rose' ? 'bg-rose-400' : 'bg-amber-400'}`} />
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-slate-800">{c.skill_name}</span>
                      <ChangeDiff change={c} />
                      <span className="text-[11px] text-slate-400">v{c.version}{c.created_at ? ` · ${String(c.created_at).slice(0, 10)}` : ''}</span>
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
        <div className="fixed inset-0 z-50 grid place-items-center p-4 bg-slate-900/40 backdrop-blur-sm" onClick={() => setEditor(null)}>
          <div className="glass p-6 w-[420px] max-w-full max-h-[90vh] overflow-auto" onClick={e => e.stopPropagation()}>
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
              <button className="btn-primary flex-1" disabled={!editor.skill_name || saving}
                onClick={() => saveEdit(editor.action, {
                  skill_name: editor.skill_name, importance: editor.importance,
                  weight: editor.weight, level_required: editor.level_required })}>
                {saving ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog open={!!removing} title={`删除能力项「${removing?.name ?? ''}」？`}
        description="删除后将记录到演化历史，可通过人工新增恢复。"
        onConfirm={() => { const sk = removing; setRemoving(null); saveEdit('remove', { skill_name: sk.name }) }}
        onCancel={() => setRemoving(null)} />
    </div>
  )
}
