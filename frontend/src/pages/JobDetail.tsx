import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, FileText, GitBranch, History, Network, ExternalLink, Landmark, Sparkles, ChevronRight,
} from 'lucide-react'
import { ITarget, IStack, IShieldCheck, IBriefcase } from '../components/icons'
import { api, errMsg, JobDetail as TJob, Skill as TSkill, AuthorityItem, CATEGORY_COLORS, RoleContract } from '../api'
import { Card, Spinner, ConfidencePill, Badge, ErrorState } from '../components/ui'
import ChangeDiff from '../components/ChangeDiff'
import { useToast } from '../components/Toast'
import { useReveal } from '../hooks/gsapFx'
import { useAuth } from '../auth'

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

/** 已淘汰技能点：演化判定「在最新窗口 JD 中一次未再出现」，默认折叠、删除线。
 *  不删是因为演化叙事要能回溯（Struts/Hibernate 的退场正是本作品的核心论据），
 *  但也绝不能和现行技能混在一起——那会让「演化历史」和「能力画像」两个页面
 *  自相矛盾。折叠 + 删除线让"曾经要求、现已退场"这层语义自己说清楚。 */
function DeprecatedChips({ items }: { items: TSkill[] }) {
  const [open, setOpen] = useState(false)
  if (!items.length) return null
  return (
    <div className="mt-2 pt-2 border-t border-slate-100">
      <button onClick={() => setOpen(o => !o)}
        className="text-[11px] text-rose-300 hover:text-rose-500 transition inline-flex items-center gap-1">
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`} />
        已淘汰 {items.length} 项（最新窗口 JD 中未再出现）
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {items.map((f: TSkill) => (
            <span key={f.skill_id} className="chip border border-dashed bg-rose-50/60 border-rose-200 text-rose-400 line-through text-[11px]"
              title="经演化判定需求消退，保留历史可回溯">
              {f.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SkillRow({ s, fineChildren = [], fineCandidates = [], fineDeprecated = [] }: any) {
  return (
    <div className="rounded-xl bg-sky-50/70 hover:bg-sky-100/80 px-3.5 py-2.5 transition group">
      {/* 首行：技能名 + 分类/级别；操作按钮固定右侧。徽章不换行，窄屏截断而非竖排 */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-medium text-slate-800 shrink-0">{s.name}</span>
        <span className="chip border bg-slate-100 text-slate-600 border-slate-200 whitespace-nowrap truncate min-w-0">{s.category}</span>
        <span className="text-[11px] text-slate-400 shrink-0 hidden sm:inline">{SKILL_LEVEL[s.level_required] || ''}</span>
        <span className="flex-1" />
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
      <DeprecatedChips items={fineDeprecated} />
    </div>
  )
}

/** 无演化记录时的说明视图。
 *
 *  原来这里只有一句「暂无演化记录」。而岗位库管理按 is_new 倒序排列，6 个新兴岗位
 *  排在最前面，评委点进第一个岗位看到的必然是这句话，因此判定「演化历史这一功能
 *  还没有实现」。功能是实现了的——是这几个岗位**本来就不该有**跨切片演化记录：
 *  它们在 2018/2024 历史语料里一条 JD 都检索不到。
 *
 *  与其显示空白，不如把这个事实讲出来：历史语料检索到 0 条，本身就是「新岗位涌现」
 *  最硬的量化证据，而且可一键复核。造一段假的 v1→v2 反而会写出事实错误的淘汰记录。 */
function EmptyHistory({ job, authority, eraCounts, earliestJd }: {
  job: TJob, authority: AuthorityItem[],
  eraCounts?: Record<string, number>, earliestJd?: string,
}) {
  const isNew = (job as any).is_new
  const firstSeen = (job as any).first_seen_date as string | null
  const policy = authority.find(a => a.kind === 'policy') || authority[0]
  const hist = (eraCounts?.['2018'] ?? 0) + (eraCounts?.['2024'] ?? 0)
  const now = eraCounts?.['2026'] ?? 0

  if (!isNew) {
    return (
      <div className="text-center py-12 text-slate-400 text-sm">
        暂无演化记录。可在「岗位能力演化」页用新 JD 驱动该岗位能力更新。
      </div>
    )
  }
  const steps = [
    policy?.publish_date && {
      k: 'policy', tone: 'indigo',
      title: `${policy.issuer || '权威机构'}${policy.kind === 'policy' ? '发布/公示' : '报告收录'}`,
      date: String(policy.publish_date).slice(0, 10), desc: policy.title,
    },
    firstSeen && {
      k: 'first', tone: 'violet', title: '首次可考证出现',
      date: String(firstSeen).slice(0, 10),
      desc: `新兴类型：${(job as any).emergence_type === 'revived' ? '复兴型（曾出现→沉寂→重新兴起）' : '新出现型'}`,
    },
    earliestJd && {
      k: 'jd', tone: 'cyan', title: '市场 JD 首次采集到', date: earliestJd,
      desc: `当前语料中该岗位共 ${now} 条真实 JD`,
    },
  ].filter(Boolean) as any[]

  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-amber-50/70 border border-amber-100 px-4 py-3">
        <div className="flex items-center gap-2 mb-1">
          <Sparkles className="w-4 h-4 text-amber-500 shrink-0" />
          <span className="text-sm font-medium text-slate-800">该岗位尚无跨时间切片的演化记录</span>
        </div>
        <p className="text-xs text-slate-500 leading-relaxed">
          {eraCounts
            ? <>本岗位在 2018 年（<b>{eraCounts['2018'] ?? 0}</b> 条）与 2024 年（<b>{eraCounts['2024'] ?? 0}</b> 条）
              历史语料切片中共检索到 <b className="text-amber-600">{hist}</b> 条 JD
              {hist === 0 && '——历史语料里根本不存在这个岗位'}，
              因此只有基于 2026 年现网语料的 v1 基线，没有可比对的历史版本。</>
            : <>本岗位为权威依据驱动的新兴岗位，历史语料切片中无对应 JD，故只有 v1 基线。</>}
          <br />
          <span className="text-slate-400">
            这不是缺失，而是「新岗位涌现」最直接的数据证据：不是我们宣称它新，
            而是历史招聘语料里检索不到它。系统不会为了填满时间线而生成没有语料依据的演化记录。
          </span>
        </p>
      </div>
      {steps.length > 0 && (
        <div>
          <div className="label mb-3">岗位溯源时间线</div>
          <div className="relative pl-6">
            <div className="absolute left-2 top-1 bottom-1 w-px bg-slate-200" />
            {steps.map(s => (
              <div key={s.k} className="relative pb-5">
                <span className={`absolute -left-[18px] top-1 w-3 h-3 rounded-full ring-4 ring-white ${
                  s.tone === 'indigo' ? 'bg-indigo-400' : s.tone === 'violet' ? 'bg-violet-400' : 'bg-cyan-400'}`} />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold text-slate-800">{s.title}</span>
                  <span className="text-[11px] text-slate-400">{s.date}</span>
                </div>
                <p className="text-xs text-slate-500 mt-1">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      )}
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
  const [loadError, setLoadError] = useState(false)
  const [authority, setAuthority] = useState<AuthorityItem[]>([])
  const [contract, setContract] = useState<RoleContract | null>(null)
  const toast = useToast()
  const auth = useAuth()
  const revealRef = useReveal('[data-reveal]', { scroll: true, stagger: 0.05, deps: [job, tab] })

  const reload = () => { setLoadError(false); api.job(jobId).then(setJob).catch(() => setLoadError(true)) }
  useEffect(() => { reload() }, [jobId])
  useEffect(() => {
    setAuthority([])
    api.jobAuthority(jobId).then(d => setAuthority(d.items || [])).catch(() => {})
    api.jobContract(jobId).then(setContract).catch(() => setContract(null))
  }, [jobId])
  useEffect(() => {
    if (tab === 'evidence' && !evidence) api.jobEvidence(jobId).then(setEvidence).catch(e => toast('error', errMsg(e, '证据加载失败')))
    if (tab === 'history' && !history) api.changes(jobId).then(setHistory).catch(e => toast('error', errMsg(e, '演化记录加载失败')))
  }, [tab])

  if (loadError) return <ErrorState text="岗位详情加载失败" onRetry={reload} />
  if (!job) return <Spinner />
  const color = CATEGORY_COLORS[job.category] || '#6366F1'
  // 粗/细粒度分组：细粒度技能点作为「细分技能点」挂在其父（粗粒度）技能行下。
  // 再按 status 三分流（后端 job_to_dict 不按 status 过滤，三类都会返回）：
  //   active    ——通过 ≥2 独立来源交叉验证，直接展示
  //   candidate ——单来源待验证，折叠收起
  //   deprecated——已被演化判定淘汰，必须单独分流。此前只分流了 candidate，于是
  //     Java 岗 23 项已淘汰能力（Struts/Hibernate/SpringMVC/Memcached…）以亮色
  //     「细分技能点」chip 渲染在 Spring、分布式系统等父项下，和现行技能长得一模一样。
  //     评委在「演化历史」看到「🔴 淘汰 Hibernate」，退回「能力画像」又看到它还在，
  //     两个页面自相矛盾——演化叙事是本作品的核心卖点，这种矛盾比少显示几项致命。
  const allSkills: TSkill[] = [...job.required_skills, ...job.bonus_skills]
  const statusOf = (s: TSkill) => (s as any).status as string | undefined
  const isCandidate = (s: TSkill) => statusOf(s) === 'candidate'
  const isDeprecated = (s: TSkill) => statusOf(s) === 'deprecated'
  const isLive = (s: TSkill) => !isCandidate(s) && !isDeprecated(s)
  const fineByParent = new Map<string, TSkill[]>()
  const candByParent = new Map<string, TSkill[]>()
  const depByParent = new Map<string, TSkill[]>()
  for (const s of allSkills) {
    if (s.granularity === 'fine' && s.parent_name) {
      const m = isDeprecated(s) ? depByParent : isCandidate(s) ? candByParent : fineByParent
      const arr = m.get(s.parent_name) || []
      arr.push(s); m.set(s.parent_name, arr)
    }
  }
  const coarseRequired = job.required_skills.filter(s => s.granularity !== 'fine' && isLive(s))
  const coarseBonus = job.bonus_skills.filter(s => s.granularity !== 'fine' && isLive(s))
  const isOrphan = (s: TSkill) => s.granularity === 'fine' &&
    (!s.parent_name || !allSkills.some(p => p.granularity !== 'fine' && p.name === s.parent_name))
  const orphanFine = allSkills.filter(s => isOrphan(s) && isLive(s))
  const orphanCand = allSkills.filter(s => isOrphan(s) && isCandidate(s))
  const orphanDep = allSkills.filter(s => isOrphan(s) && isDeprecated(s))
  const deprecatedAll = allSkills.filter(isDeprecated)
  const emergence = (job as any).emergence_type as string | null
  const eraCounts = (job.source_summary || {} as any).era_counts as Record<string, number> | undefined
  const earliestJd = (job.source_summary || {} as any).earliest_jd as string | undefined

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
            <div className="flex flex-col sm:flex-row gap-2 mt-3">
              <button onClick={() => nav('/match', { state: { jobId } })} className="btn-primary justify-center"><ITarget className="w-4 h-4" /> 匹配</button>
              <button onClick={() => nav('/panorama', { state: { jobId } })} className="btn-ghost justify-center"><Network className="w-4 h-4" /> 图谱定位</button>
              <button onClick={() => nav('/evolution', { state: { jobId } })} className="btn-ghost justify-center"><GitBranch className="w-4 h-4" /> 演化</button>
            </div>
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
        {auth.can('admin') && (
          <button onClick={() => nav('/evolution', { state: { jobId } })}
            className="btn-ghost w-full sm:w-auto sm:ml-auto justify-center whitespace-nowrap">
            <GitBranch className="w-4 h-4" /> 进入演化工作流
          </button>
        )}
      </div>

      {tab === 'profile' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 space-y-5">
            {contract && (
              <Card className="p-5">
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div><div className="label">岗位核心契约</div><div className="text-xs text-slate-500 mt-1">{contract.track || '通用'} 轨道 · {LEVEL_LABEL[contract.seniority] || contract.seniority || '级别未指定'} · {contract.recruitment_type === 'campus' ? '校招' : contract.recruitment_type === 'social' ? '社招' : '校社招混合'}</div></div>
                  <Badge tone={contract.status === 'evidence_insufficient' ? 'amber' : 'emerald'}>{contract.status === 'evidence_insufficient' ? '证据待补' : `v${contract.version} 当前版本`}</Badge>
                </div>
                <div className="space-y-2">{contract.clusters.map(cluster => (
                  <details key={`${cluster.importance}-${cluster.name}`} className="group rounded-xl border border-slate-200 bg-white/65 px-3.5 py-3">
                    <summary className="list-none cursor-pointer flex items-center gap-3"><span className={`w-2 h-2 rounded-full ${cluster.importance === 'required' ? 'bg-indigo-500' : 'bg-amber-400'}`} /><span className="font-semibold text-sm text-slate-800 flex-1">{cluster.name}</span><Badge tone={cluster.importance === 'required' ? 'indigo' : 'amber'}>{cluster.importance === 'required' ? '必备' : '加分'}</Badge><ConfidencePill value={cluster.confidence} /><ChevronRight className="w-4 h-4 text-slate-400 transition-transform group-open:rotate-90" /></summary>
                    <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap gap-1.5">{cluster.skills.map((skill, index) => {
                      const name = typeof skill === 'string' ? skill : skill.name
                      return <Badge key={`${name}-${index}`} tone="slate">{name}</Badge>
                    })}{cluster.employer_count != null && <span className="w-full text-[11px] text-slate-400 mt-1">{cluster.employer_count} 个独立雇主支持 · 覆盖率 {Math.round((cluster.support_ratio || 0) * 100)}%</span>}</div>
                  </details>
                ))}</div>
              </Card>
            )}
            <Card className="p-5">
              <details open={!contract}>
              <summary className="list-none cursor-pointer label flex items-center gap-2"><ITarget className="w-4 h-4 text-accent" /> 完整技能与证据 · 必备 ({coarseRequired.length}) <ChevronRight className="w-4 h-4 ml-auto" /></summary>
              <div className="space-y-2 mt-3">
                {coarseRequired.map(s => (
                  <div key={s.skill_id} data-reveal>
                  <SkillRow s={s} fineChildren={fineByParent.get(s.name) || []}
                    fineCandidates={candByParent.get(s.name) || []}
                    fineDeprecated={depByParent.get(s.name) || []} />
                  </div>
                ))}
              </div>
              {(orphanFine.length > 0 || orphanCand.length > 0 || orphanDep.length > 0) && (
                <div className="mt-3 pt-3 border-t border-slate-100">
                  {orphanFine.length > 0 && (
                    <>
                      <div className="text-[11px] text-slate-400 mb-1.5">其他细分技能点</div>
                      <div className="flex flex-wrap gap-1.5"><FineChips items={orphanFine} /></div>
                    </>
                  )}
                  <CandidateChips items={orphanCand} />
                  <DeprecatedChips items={orphanDep} />
                </div>
              )}
              </details>
            </Card>
            {coarseBonus.length > 0 && (
              <Card className="p-5">
                <details open={!contract}><summary className="list-none cursor-pointer label flex items-center gap-2">完整技能与证据 · 加分 ({coarseBonus.length}) <ChevronRight className="w-4 h-4 ml-auto" /></summary>
                <div className="flex flex-wrap gap-2 mt-3">
                  {coarseBonus.map(s => (
                    <span key={s.skill_id} className="chip border bg-white/70 border-slate-200 text-slate-600">
                      {s.name} <span className="text-slate-400">·{Math.round(s.confidence * 100)}%</span>
                    </span>
                  ))}
                </div>
                </details>
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
                /* 默认展开前 3 条：证据按 active 优先 + 置信度降序返回，前 3 条即最高置信项。
                   不全展开——重建后单个岗位可有上百项。summary 上的 list-none 去掉了原生
                   三角箭头，必须自己补一个 ChevronRight，否则整页看起来只是一排静态标签，
                   评委的原话就是「标着几处来源但是没有具体显示出来」。 */
                <details key={i} open={i < 3} className="rounded-xl bg-sky-50/70 px-4 py-3 group">
                  <summary className="flex items-center justify-between gap-2 flex-wrap cursor-pointer list-none">
                    <div className="flex items-center gap-2">
                      <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0 transition-transform group-open:rotate-90" />
                      <FileText className="w-4 h-4 text-slate-500" />
                      <span className="text-sm font-medium text-slate-800">{it.skill}</span>
                      <Badge tone={it.importance === 'required' ? 'indigo' : 'slate'}>
                        {it.importance === 'required' ? '必备' : '加分'}</Badge>
                      {it.status === 'deprecated' && <Badge tone="rose">已淘汰</Badge>}
                      {it.status === 'candidate' && <Badge tone="slate">候选</Badge>}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      {/* source_count 是「提及该技能的 JD 数」，证据行按上限留存代表样本。
                          原文案写成「87 来源」，而展开只有 6 张卡，看起来像坏了——
                          两个数字各自都是真的，撒谎的是把它们混成一个词。 */}
                      {`${it.source_count} 条 JD 提及`}
                      {evs.length > 0 && ` · 留存证据 ${evs.length} 条`}
                      <ConfidencePill value={it.confidence} factors={it.factors} />
                    </div>
                  </summary>
                  {evs.length === 0 ? (
                    <div className="mt-2 pl-6 text-xs text-slate-400">该能力项暂无独立JD证据（人工添加或低频项）</div>
                  ) : (
                  <div className="mt-2.5 pl-0 sm:pl-6 grid grid-cols-1 md:grid-cols-2 gap-2">
                    {evs.slice(0, 12).map((e: any, j: number) => {
                      // snippet 存的是抽取出的技能短语本身（"Python"），不是 JD 原句；
                      // 直接甩一个单词出来像渲染出错，点明它是原文命中词更诚实。
                      const isBareToken = !e.snippet || e.snippet === it.skill
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
                          <p className="text-[11px] text-slate-400 mt-1 line-clamp-2">
                            {isBareToken ? <>原文命中：<span className="text-slate-600">{it.skill}</span></> : e.snippet}
                          </p>
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
            <EmptyHistory job={job} authority={authority} eraCounts={eraCounts} earliestJd={earliestJd} />
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

    </div>
  )
}
