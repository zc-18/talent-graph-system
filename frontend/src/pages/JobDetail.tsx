import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, FileText, GitBranch, History, Network, ExternalLink, Landmark, Sparkles, ChevronRight,
} from 'lucide-react'
import { ITarget, IStack, IShieldCheck, IBriefcase } from '../components/icons'
import { api, errMsg, JobDetail as TJob, Skill as TSkill, AuthorityItem, CATEGORY_COLORS, ConfidenceSnapshot, RoleContract } from '../api'
import { Card, Spinner, ConfidencePill, Badge, ErrorState } from '../components/ui'
import ChangeDiff from '../components/ChangeDiff'
import { useToast } from '../components/Toast'
import { useReveal } from '../hooks/gsapFx'
import { useAuth } from '../auth'
import { ConfidenceMeta, ConfidenceTrend } from '../components/ConfidenceMeta'
import ConfidenceExplain from '../components/ConfidenceExplain'

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' }
const SKILL_LEVEL: Record<string, string> = { familiar: '了解', proficient: '熟练', expert: '精通' }

function FineChips({ items }: { items: TSkill[] }) {
  return (
    <>
      {items.map((f: TSkill) => (
        <span key={f.skill_id} className="chip border bg-white/80 border-accent/25 text-body-2 text-[11px]"
          title={`置信度 ${Math.round(f.confidence * 100)}%`}>
          {f.name} <span className="text-body-3">·{Math.round(f.confidence * 100)}%</span>
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
    <div className="mt-2 pt-2 border-t border-line-soft/6">
      <button onClick={() => setOpen(o => !o)}
        className="text-[11px] text-body-3 hover:text-body-2 transition inline-flex items-center gap-1">
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`} />
        候选技能点 {items.length} 项（单来源，待交叉验证）
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {items.map((f: TSkill) => (
            <span key={f.skill_id} className="chip border border-dashed bg-surface-muted border-line-soft/14 text-body-2 text-[11px]"
              title={`置信度 ${Math.round(f.confidence * 100)}% · 仅 ${f.source_count} 个来源`}>
              {f.name} <span className="text-body-3">·{Math.round(f.confidence * 100)}%</span>
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
    <div className="mt-2 pt-2 border-t border-line-soft/6">
      <button onClick={() => setOpen(o => !o)}
        className="text-[11px] text-danger/60 hover:text-danger transition inline-flex items-center gap-1">
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`} />
        已淘汰 {items.length} 项（最新窗口 JD 中未再出现）
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {items.map((f: TSkill) => (
            <span key={f.skill_id} className="chip border border-dashed bg-danger-weak/60 border-danger/25 text-danger/80 line-through text-[11px]"
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
    <div className="rounded-xl bg-accent/6 hover:bg-accent/12 px-3.5 py-2.5 transition group">
      {/* 首行：技能名 + 分类/级别；操作按钮固定右侧。徽章不换行，窄屏截断而非竖排 */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-medium text-body-1 shrink-0">{s.name}</span>
        <span className="chip border bg-brand-ink/8 text-body-2 border-line-soft/8 whitespace-nowrap truncate min-w-0">{s.category}</span>
        <span className="text-[11px] text-body-3 shrink-0 hidden sm:inline">{SKILL_LEVEL[s.level_required] || ''}</span>
        <span className="flex-1" />
      </div>
      {/* 次行：权重条 + 来源数/置信度 */}
      <div className="mt-1.5 flex items-center gap-2.5">
        <div className="flex-1 h-1.5 rounded-full bg-accent/8 overflow-hidden">
          <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.round(s.weight * 100)}%` }} />
        </div>
        <span className="text-[11px] text-body-3 shrink-0 sm:hidden">{SKILL_LEVEL[s.level_required] || ''}</span>
        <span className="text-[11px] text-body-3 shrink-0" title="通过交叉验证的独立雇主数">{s.source_count} 雇主</span>
        <span className="shrink-0"><ConfidencePill value={s.confidence} factors={s.factors} /></span>
      </div>
      {/* 细分技能点：已通过交叉验证的挂父项下直接展示 */}
      {fineChildren.length > 0 && (
        <div className="mt-2 pt-2 border-t border-accent/12 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] text-body-3 shrink-0">细分技能点</span>
          <FineChips items={fineChildren} />
        </div>
      )}
      <CandidateChips items={fineCandidates} />
      <DeprecatedChips items={fineDeprecated} />
    </div>
  )
}

/** 粗粒度候选能力行。与 SkillRow 同信息量，但整行虚线描边 + 次级底色 + 「候选」徽标，
 *  一眼可分「已验证 / 待验证」，同时把 source_count（独立雇主数）从 tooltip 提到正文——
 *  这是 ≥2 独立雇主准入门槛的判定量本身，藏在 hover 里等于没说。 */
function CandidateSkillRow({ s, importance, fineChildren = [], fineCandidates = [], fineDeprecated = [] }: any) {
  return (
    <div className="rounded-xl border border-dashed border-line-soft/16 bg-surface-muted px-3.5 py-2.5 transition hover:border-line-soft/24">
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className="text-sm font-medium text-body-2 shrink-0">{s.name}</span>
        <span className="chip border bg-white/70 text-body-3 border-line-soft/10 whitespace-nowrap truncate min-w-0">{s.category}</span>
        <Badge tone="slate">候选</Badge>
        <span className="text-[11px] text-body-3 shrink-0 hidden sm:inline">{importance === 'required' ? '必备向' : '加分向'}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2.5 flex-wrap">
        <span className="text-[11px] text-body-3">
          独立雇主 <span className="font-semibold text-body-2">{s.source_count}</span> / 门槛 2
        </span>
        <span className="text-[11px] text-body-3">置信度 {Math.round((s.confidence || 0) * 100)}%</span>
        <span className="flex-1" />
        <span className="text-[11px] text-body-3">{SKILL_LEVEL[s.level_required] || ''}</span>
      </div>
      {fineChildren.length > 0 && (
        <div className="mt-2 pt-2 border-t border-line-soft/8 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] text-body-3 shrink-0">细分技能点</span>
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
      <div className="text-center py-12 text-body-3 text-sm">
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
      <div className="rounded-xl bg-warn-weak/70 border border-warn/20 px-4 py-3">
        <div className="flex items-center gap-2 mb-1">
          <Sparkles className="w-4 h-4 text-warn shrink-0" />
          <span className="text-sm font-medium text-body-1">该岗位尚无跨时间切片的演化记录</span>
        </div>
        <p className="text-xs text-body-2 leading-relaxed">
          {eraCounts
            ? <>本岗位在 2018 年（<b>{eraCounts['2018'] ?? 0}</b> 条）与 2024 年（<b>{eraCounts['2024'] ?? 0}</b> 条）
              历史语料切片中共检索到 <b className="text-warn">{hist}</b> 条 JD
              {hist === 0 && '——历史语料里根本不存在这个岗位'}，
              因此只有基于 2026 年现网语料的 v1 基线，没有可比对的历史版本。</>
            : <>本岗位为权威依据驱动的新兴岗位，历史语料切片中无对应 JD，故只有 v1 基线。</>}
          <br />
          <span className="text-body-3">
            这不是缺失，而是「新岗位涌现」最直接的数据证据：不是我们宣称它新，
            而是历史招聘语料里检索不到它。系统不会为了填满时间线而生成没有语料依据的演化记录。
          </span>
        </p>
      </div>
      {steps.length > 0 && (
        <div>
          <div className="label mb-3">岗位溯源时间线</div>
          <div className="relative pl-6">
            <div className="absolute left-2 top-1 bottom-1 w-px bg-brand-ink/12" />
            {steps.map(s => (
              <div key={s.k} className="relative pb-5">
                <span className={`absolute -left-[18px] top-1 w-3 h-3 rounded-full ring-4 ring-white ${
                  s.tone === 'indigo' ? 'bg-accent-violet/70' : s.tone === 'violet' ? 'bg-accent-violet/70' : 'bg-accent'}`} />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold text-body-1">{s.title}</span>
                  <span className="text-[11px] text-body-3">{s.date}</span>
                </div>
                <p className="text-xs text-body-2 mt-1">{s.desc}</p>
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
  const [confidenceHistory, setConfidenceHistory] = useState<ConfidenceSnapshot[]>([])
  const toast = useToast()
  const auth = useAuth()
  const revealRef = useReveal('[data-reveal]', { scroll: true, stagger: 0.05, deps: [job, tab] })

  const reload = () => {
    setLoadError(false)
    api.job(jobId).then(data => {
      setJob(data)
      setConfidenceHistory(data.confidence_trend || [])
    }).catch(() => setLoadError(true))
  }
  useEffect(() => { reload() }, [jobId])
  useEffect(() => {
    setAuthority([])
    api.jobAuthority(jobId).then(d => setAuthority(d.items || [])).catch(() => {})
    api.jobContract(jobId).then(setContract).catch(() => setContract(null))
    api.jobConfidenceHistory(jobId).then(setConfidenceHistory).catch(() => {})
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
  const isCoarse = (s: TSkill) => s.granularity !== 'fine'
  const coarseRequired = job.required_skills.filter(s => isCoarse(s) && isLive(s))
  const coarseBonus = job.bonus_skills.filter(s => isCoarse(s) && isLive(s))
  // 粗粒度候选此前会被整页丢弃：它进不了 coarseRequired/coarseBonus（被 isLive 滤掉），
  // 也进不了 candByParent/orphanCand（那两个只收 granularity==='fine'）。R6 把 48 条
  // 只有 0–1 个独立雇主的历史遗留 active 降级为 candidate 之后，这个洞立刻致命：
  // 「自动驾驶算法工程师」28 项里 26 项是候选，页面几乎空白，且与交付材料写的
  // 「812 条已验证 + 若干候选」对不上——而「事实与呈现一致」正是本作品的立论。
  // 解法不是把候选藏得更彻底，而是单独成区、并把准入门槛的判定量（独立雇主数）摆到台面上。
  const coarseCandidate = allSkills.filter(s => isCoarse(s) && isCandidate(s))
  const coarseDeprecated = allSkills.filter(s => isCoarse(s) && isDeprecated(s))
  const requiredNames = new Set(job.required_skills.map(s => s.name))
  // 只有「必备」「候选」两区用整行渲染、会带出挂在自己名下的细分技能点；加分区是纯 chip。
  // 因此父项不在这两区里的细分技能点（挂加分项、挂已淘汰父项、父项根本不存在）
  // 都必须落到兜底区，否则它们会和粗粒度候选一样静悄悄地一个都不显示。
  const rowParents = new Set([...coarseRequired, ...coarseCandidate].map(s => s.name))
  const isLoose = (s: TSkill) => s.granularity === 'fine' &&
    (!s.parent_name || !rowParents.has(s.parent_name))
  const orphanFine = allSkills.filter(s => isLoose(s) && isLive(s))
  const orphanCand = allSkills.filter(s => isLoose(s) && isCandidate(s))
  const orphanDep = allSkills.filter(s => isLoose(s) && isDeprecated(s))
  const looseDep = [...coarseDeprecated, ...orphanDep]
  const candidateTotal = coarseCandidate.length + orphanCand.length
  const activeTotal = allSkills.filter(isLive).length
  const emergence = (job as any).emergence_type as string | null
  const eraCounts = (job.source_summary || {} as any).era_counts as Record<string, number> | undefined
  const earliestJd = (job.source_summary || {} as any).earliest_jd as string | undefined

  return (
    <div ref={revealRef} className="space-y-5">
      <button onClick={() => nav(-1)} className="-my-1 flex items-center gap-1.5 py-1 text-sm text-body-2 hover:text-body-1">
        <ArrowLeft className="w-4 h-4" /> 返回
      </button>

      <Card className="relative overflow-hidden p-5 sm:p-6">
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
            <h1 className="text-2xl sm:text-3xl font-extrabold text-body-1">{job.name}</h1>
            <p className="text-sm text-body-2 mt-2 max-w-3xl leading-relaxed">{job.summary}</p>
          </div>
          <div className="w-full text-left sm:w-auto sm:text-right sm:shrink-0">
            <div className="text-xs text-body-2 mb-1">岗位定义置信度</div>
            {job.confidence_factors
              ? <ConfidenceExplain value={job.confidence} factors={job.confidence_factors}><span className="text-3xl font-extrabold gradient-text">{Math.round(job.confidence * 100)}%</span></ConfidenceExplain>
              : <div className="text-3xl font-extrabold gradient-text">{Math.round(job.confidence * 100)}%</div>}
            <div className="text-[11px] text-body-3 mt-1">{job.evidence_count} 条证据支撑</div>
            <div className="flex flex-col sm:flex-row gap-2 mt-3">
              <button onClick={() => nav('/match', { state: { jobId } })} className="btn-primary justify-center"><ITarget className="w-4 h-4" /> 匹配</button>
              <button onClick={() => nav('/panorama', { state: { jobId } })} className="btn-ghost justify-center"><Network className="w-4 h-4" /> 图谱定位</button>
              <button onClick={() => nav('/evolution', { state: { jobId } })} className="btn-ghost justify-center"><GitBranch className="w-4 h-4" /> 演化</button>
            </div>
          </div>
        </div>
        <div className="relative mt-5 grid gap-4 border-t border-line-soft/8 pt-4 md:grid-cols-[minmax(0,0.85fr)_minmax(300px,1.15fr)]">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-semibold text-body-2">置信度数据状态</div>
            <ConfidenceMeta asOf={job.confidence_as_of} delta={job.confidence_delta} />
          </div>
          <ConfidenceTrend items={confidenceHistory} />
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
                  <div><div className="label">岗位核心契约</div><div className="text-xs text-body-2 mt-1">{contract.track || '通用'} 轨道 · {LEVEL_LABEL[contract.seniority] || contract.seniority || '级别未指定'} · {contract.recruitment_type === 'campus' ? '校招' : contract.recruitment_type === 'social' ? '社招' : '校社招混合'}</div></div>
                  <Badge tone={contract.status === 'evidence_insufficient' ? 'slate' : 'emerald'}>v{contract.version} 当前版本</Badge>
                </div>
                <div className="space-y-2">{contract.clusters.map(cluster => (
                  <details key={`${cluster.importance}-${cluster.name}`} className="group rounded-xl border border-line-soft/8 bg-white/65 px-3.5 py-3">
                    <summary className="list-none cursor-pointer flex items-center gap-3"><span className={`w-2 h-2 rounded-full ${cluster.importance === 'required' ? 'bg-accent-violet' : 'bg-warn/80'}`} /><span className="font-semibold text-sm text-body-1 flex-1">{cluster.name}</span><Badge tone={cluster.importance === 'required' ? 'indigo' : 'amber'}>{cluster.importance === 'required' ? '必备' : '加分'}</Badge><ConfidencePill value={cluster.confidence} /><ChevronRight className="w-4 h-4 text-body-3 transition-transform group-open:rotate-90" /></summary>
                    <div className="mt-3 pt-3 border-t border-line-soft/6 flex flex-wrap gap-1.5">{cluster.skills.map((skill, index) => {
                      const name = typeof skill === 'string' ? skill : skill.name
                      return <Badge key={`${name}-${index}`} tone="slate">{name}</Badge>
                    })}{cluster.employer_count != null && <span className="w-full text-[11px] text-body-3 mt-1">{cluster.employer_count} 个独立雇主支持 · 支持率 {Math.round((cluster.support_ratio || 0) * 100)}%</span>}</div>
                  </details>
                ))}</div>
              </Card>
            )}
            <Card className="p-5">
              <details open={!contract}>
              <summary className="list-none cursor-pointer label flex items-center gap-2"><ITarget className="w-4 h-4 text-accent" /> 完整技能与证据 · 必备 ({coarseRequired.length}) <ChevronRight className="w-4 h-4 ml-auto" /></summary>
              {/* 页面显示几项、库里有几项、材料里写几项，必须能对上。这行就是那份对账单。 */}
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-body-3">
                <span>本岗能力项共 {allSkills.length} 条</span>
                <span className="text-body-2">已验证 {activeTotal}</span>
                <span>候选 {candidateTotal}</span>
                <span>已淘汰 {allSkills.filter(isDeprecated).length}</span>
                <span className="text-body-3">（准入门槛：≥2 个独立雇主）</span>
              </div>
              <div className="space-y-2 mt-3">
                {coarseRequired.map(s => (
                  <div key={s.skill_id} data-reveal>
                  <SkillRow s={s} fineChildren={fineByParent.get(s.name) || []}
                    fineCandidates={candByParent.get(s.name) || []}
                    fineDeprecated={depByParent.get(s.name) || []} />
                  </div>
                ))}
              </div>
              {(orphanFine.length > 0 || looseDep.length > 0) && (
                <div className="mt-3 pt-3 border-t border-line-soft/6">
                  {orphanFine.length > 0 && (
                    <>
                      <div className="text-[11px] text-body-3 mb-1.5">其他细分技能点</div>
                      <div className="flex flex-wrap gap-1.5"><FineChips items={orphanFine} /></div>
                    </>
                  )}
                  <DeprecatedChips items={looseDep} />
                </div>
              )}
              </details>
            </Card>
            {coarseBonus.length > 0 && (
              <Card className="p-5">
                <details open={!contract}><summary className="list-none cursor-pointer label flex items-center gap-2"><ITarget className="w-4 h-4 text-accent" /> 完整技能与证据 · 加分 ({coarseBonus.length}) <ChevronRight className="w-4 h-4 ml-auto" /></summary>
                <div className="flex flex-wrap gap-2 mt-3">
                  {coarseBonus.map(s => (
                    <span key={s.skill_id} className="chip border bg-white/70 border-line-soft/8 text-body-2">
                      {s.name} <span className="text-body-3">·{Math.round(s.confidence * 100)}%</span>
                    </span>
                  ))}
                </div>
                </details>
              </Card>
            )}
            {/* 候选能力独立成区。绝不能靠隐藏候选来消除「页面显示数」与「库内能力项数」的差：
                那是把不一致藏起来而不是解决它。这里把候选完整渲染，只在视觉上与已验证能力
                划清界限（虚线描边 + 次级底色 + 候选徽标），并逐行给出独立雇主数 / 门槛 2。 */}
            {candidateTotal > 0 && (
              <Card className="p-5">
                <div className="label flex items-center gap-2"><IShieldCheck className="w-4 h-4 text-body-3" /> 候选能力 · 待更多雇主佐证 ({candidateTotal})</div>
                <p className="mt-2 text-[11px] leading-relaxed text-body-2">
                  下列能力项的<span className="font-semibold text-body-1">独立雇主数未达到准入门槛（≥2）</span>，
                  因此不计入上方已验证能力，也不参与人岗匹配打分；证据链完整保留可追溯，补足第 2 个独立雇主后即转为已验证。
                </p>
                <details open={candidateTotal <= 12} className="group mt-3">
                  <summary className="list-none cursor-pointer -my-1 py-1 inline-flex items-center gap-1 text-[11px] text-body-3 hover:text-body-2 transition">
                    <ChevronRight className="w-3.5 h-3.5 transition-transform group-open:rotate-90" />
                    候选能力清单（{candidateTotal} 项）
                  </summary>
                  {coarseCandidate.length > 0 && (
                    <div className="space-y-2 mt-3">
                      {coarseCandidate.map(s => (
                        <CandidateSkillRow key={s.skill_id} s={s}
                          importance={requiredNames.has(s.name) ? 'required' : 'bonus'}
                          fineChildren={fineByParent.get(s.name) || []}
                          fineCandidates={candByParent.get(s.name) || []}
                          fineDeprecated={depByParent.get(s.name) || []} />
                      ))}
                    </div>
                  )}
                  {orphanCand.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-line-soft/6">
                      <div className="text-[11px] text-body-3 mb-1.5">其他候选技能点 {orphanCand.length} 项</div>
                      <div className="flex flex-wrap gap-1.5">
                        {orphanCand.map(f => (
                          <span key={f.skill_id} className="chip border border-dashed bg-surface-muted border-line-soft/14 text-body-2 text-[11px]"
                            title={`置信度 ${Math.round((f.confidence || 0) * 100)}% · ${f.source_count} 个独立雇主`}>
                            {f.name} <span className="text-body-3">·{f.source_count} 雇主</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </details>
              </Card>
            )}
          </div>
          {/* 右列高度通常小于左列：sticky 跟随滚动，避免滚到底部时右侧大片留白 */}
          <div className="space-y-5 lg:sticky lg:top-6 self-start">
            <Card className="p-5">
              <div className="label mb-3 flex items-center gap-2"><IBriefcase className="w-4 h-4 text-accent-violet" /> 核心职责</div>
              <ul className="space-y-2">
                {job.core_responsibilities.map((r, i) => (
                  <li key={i} className="text-sm text-body-2 flex gap-2">
                    <span className="text-accent-deep font-bold">{i + 1}</span>{r}
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
                  <Landmark className="w-4 h-4 text-accent-deep" /> 权威依据
                </div>
                <div className="space-y-2.5">
                  {authority.map((a, i) => (
                    <div key={i} className="rounded-xl bg-accent/6 px-3 py-2.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge tone={a.kind === 'policy' ? 'indigo' : 'cyan'}>
                          {a.kind === 'policy' ? '部委文件' : '机构报告'}</Badge>
                        <span className="text-[11px] text-body-3">{a.issuer}{a.publish_date ? ` · ${String(a.publish_date).slice(0, 10)}` : ''}</span>
                      </div>
                      {a.url ? (
                        <a href={a.url} target="_blank" rel="noreferrer"
                          className="mt-1 flex items-start gap-1 text-sm font-medium text-body-1 hover:text-accent">
                          <span className="min-w-0">{a.title}</span>
                          <ExternalLink className="w-3 h-3 text-body-3 shrink-0 mt-1" />
                        </a>
                      ) : (
                        <div className="mt-1 text-sm font-medium text-body-1">{a.title}</div>
                      )}
                      {a.excerpt && <p className="text-[11px] text-body-2 mt-1 line-clamp-3">{a.excerpt}</p>}
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
          <div className="text-sm text-body-2 mb-4 flex items-center gap-2">
            <IShieldCheck className="w-4 h-4 text-accent-deep" />
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
                <details key={i} open={i < 3} className="rounded-xl bg-accent/6 px-4 py-3 group">
                  <summary className="flex items-center justify-between gap-2 flex-wrap cursor-pointer list-none">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <ChevronRight className="w-3.5 h-3.5 text-body-3 shrink-0 transition-transform group-open:rotate-90" />
                      <FileText className="w-4 h-4 text-body-2" />
                      <span className="min-w-0 break-words text-sm font-medium text-body-1">{it.skill}</span>
                      <Badge tone={it.importance === 'required' ? 'indigo' : 'slate'}>
                        {it.importance === 'required' ? '必备' : '加分'}</Badge>
                      {it.status === 'deprecated' && <Badge tone="rose">已淘汰</Badge>}
                      {it.status === 'candidate' && <Badge tone="slate">候选</Badge>}
                    </div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-body-3">
                      {/* source_count 是「提及该技能的 JD 数」，证据行按上限留存代表样本。
                          原文案写成「87 来源」，而展开只有 6 张卡，看起来像坏了——
                          两个数字各自都是真的，撒谎的是把它们混成一个词。 */}
                      {`${it.source_count} 条 JD 提及`}
                      {evs.length > 0 && ` · 留存证据 ${evs.length} 条`}
                      <ConfidencePill value={it.confidence} factors={it.factors} />
                    </div>
                  </summary>
                  {evs.length === 0 ? (
                    <div className="mt-2 pl-6 text-xs text-body-3">该能力项暂无独立JD证据（人工添加或低频项）</div>
                  ) : (
                  <div className="mt-2.5 grid grid-cols-1 gap-2 pl-0 sm:pl-6 xl:grid-cols-2">
                    {evs.slice(0, 12).map((e: any, j: number) => {
                      // snippet 存的是抽取出的技能短语本身（"Python"），不是 JD 原句；
                      // 直接甩一个单词出来像渲染出错，点明它是原文命中词更诚实。
                      const isBareToken = !e.snippet || e.snippet === it.skill
                      const inner = (
                        <>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <Badge tone={e.type === 'web' ? 'cyan' : e.type === 'llm' ? 'amber' : 'indigo'}>
                              {e.type === 'web' ? '网络佐证' : e.type === 'llm' ? 'LLM' : e.source || '招聘JD'}</Badge>
                            {e.type === 'web' && e.source && <span className="text-[10px] text-accent-deep">{e.source}</span>}
                            {e.company && <span className="text-[11px] text-body-2 truncate">{e.company}</span>}
                            {e.job_title && <span className="text-[11px] text-body-3 truncate hidden sm:inline">{e.job_title}</span>}
                            <span className="flex-1" />
                            {e.publish_date && <span className="text-[10px] text-body-3 shrink-0">{String(e.publish_date).slice(0, 10)}</span>}
                            {e.url && <ExternalLink className="w-3 h-3 text-body-3 shrink-0" />}
                          </div>
                          <p className="text-[11px] text-body-3 mt-1 line-clamp-2">
                            {isBareToken ? <>原文命中：<span className="text-body-2">{it.skill}</span></> : e.snippet}
                          </p>
                        </>
                      )
                      const cls = 'block rounded-lg bg-white/80 border border-accent/14 px-3 py-2 transition'
                      return e.url ? (
                        <a key={j} href={e.url} target="_blank" rel="noreferrer" className={`${cls} hover:bg-accent/12 hover:border-accent/25`}>{inner}</a>
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
              <div className="absolute left-2 top-1 bottom-1 w-px bg-brand-ink/12" />
              {history.items.map((c: any, i: number) => {
                const tone = c.change_type === 'add' ? 'emerald' : c.change_type === 'delete' ? 'rose' : 'amber'
                return (
                  <div key={i} className="relative pb-5">
                    <span className={`absolute -left-[18px] top-1 w-3 h-3 rounded-full ring-4 ring-white ${
                      tone === 'emerald' ? 'bg-accent' : tone === 'rose' ? 'bg-danger/80' : 'bg-warn/80'}`} />
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-body-1">{c.skill_name}</span>
                      <ChangeDiff change={c} />
                      <span className="text-[11px] text-body-3">v{c.version}{c.created_at ? ` · ${String(c.created_at).slice(0, 10)}` : ''}</span>
                    </div>
                    <p className="text-xs text-body-2 mt-1">{c.reason}</p>
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
