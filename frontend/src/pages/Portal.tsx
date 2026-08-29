import { useEffect, useState } from 'react'
import {
  ArrowRight, BriefcaseBusiness, ChevronRight, ClipboardCheck, Database,
  ExternalLink, GitBranch, LogIn, Network, RefreshCcw, SearchCheck, ShieldCheck,
  UserPlus, UsersRound,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { roleHome, useAuth } from '../auth'
import { api } from '../api'
import type { JobListItem, PublicStats, RoleContractCluster } from '../api'

const PRINCIPLES = [
  { icon: ShieldCheck, title: '证据先于结论', text: '岗位定义保留来源、版本和置信依据，关键判断可以回到原始证据。' },
  { icon: Network, title: '能力关系可计算', text: '把散落在 JD、简历和团队中的能力语言，整理为同一套岗位图谱。' },
  { icon: GitBranch, title: '变化持续可追踪', text: '新增证据进入审核与演化流程，岗位能力不再停留在一次性报告。' },
]

/* 六个环节不是并列关系：治理 / 发布 / 匹配 是主干（对外可见的产出），
   发现、盘点、回流是支线。节点尺寸按 primary 区分，避免六张等宽等高的模板感。 */
const FLOW: Array<{
  icon: typeof Database; title: string; text: string; primary: boolean
}> = [
  { icon: Database, title: '数据治理', text: '采集、去重、雇主归一与证据 URL 校验', primary: true },
  { icon: SearchCheck, title: '发现与演化', text: '识别候选岗位，比较真实时间切片', primary: false },
  { icon: ClipboardCheck, title: '审核发布', text: '编辑、复核、版本快照与发布门', primary: true },
  { icon: Network, title: '图谱匹配', text: '岗位契约、能力簇与差距诊断', primary: true },
  { icon: UsersRound, title: '团队盘点', text: 'HR Top-K、团队覆盖与人才结构', primary: false },
  { icon: RefreshCcw, title: '反馈回流', text: '反馈关联证据与版本后进入重算', primary: false },
]

type LoadState = 'loading' | 'ready' | 'unavailable'

/** 门户数据条：真实拉取 /api/public/stats（后端字段白名单，未登录可访问）。
    任何失败仍然安静降级为 unavailable，整条不渲染 —— 绝不回落到 0 或写死数字。 */
function usePortalStats(enabled: boolean) {
  const [stats, setStats] = useState<PublicStats | null>(null)
  const [state, setState] = useState<LoadState>('loading')
  useEffect(() => {
    if (!enabled) return
    let alive = true
    api.publicStats()
      .then(data => { if (alive) { setStats(data); setState('ready') } })
      .catch(() => { if (alive) setState('unavailable') })
    return () => { alive = false }
  }, [enabled])
  return { stats, state }
}

type Showcase = {
  job: JobListItem
  clusters: RoleContractCluster[]
  evidences: Array<{ url: string; company: string; source: string; publishDate: string; skill: string }>
}

/** 证据可核验演示：取语料最厚的一个真实岗位，展示它的能力簇与可点击的证据 URL。
    任何一步拿不到数据（未登录 / 接口异常 / 该岗位无有效 URL）都返回 null，整段不渲染。 */
function useEvidenceShowcase(enabled: boolean) {
  const [data, setData] = useState<Showcase | null>(null)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    const load = async () => {
      try {
        const list = await api.jobs({ size: 30 })
        const job = [...(list.items || [])]
          .filter(item => (item.evidence_count || 0) > 0)
          .sort((a, b) =>
            (b.employer_count || 0) - (a.employer_count || 0) ||
            (b.evidence_count || 0) - (a.evidence_count || 0))[0]
        if (!job) return
        const [contract, evidence] = await Promise.all([api.jobContract(job.id), api.jobEvidence(job.id)])
        const clusters = (contract?.clusters || []).slice(0, 4)
        const seen = new Set<string>()
        const evidences: Showcase['evidences'] = []
        for (const item of (evidence?.items || [])) {
          for (const row of (item.evidences || [])) {
            const url = String(row?.url || '')
            // 只收真实可跳转的外链，并按 URL 去重（同一条 JD 会支撑多个能力项）
            if (!/^https?:\/\//i.test(url) || seen.has(url)) continue
            seen.add(url)
            evidences.push({
              url,
              company: row.company || '',
              source: row.source || '',
              publishDate: row.publish_date || '',
              skill: item.skill || '',
            })
          }
        }
        if (!clusters.length || !evidences.length) return
        if (alive) setData({ job, clusters, evidences: evidences.slice(0, 5) })
      } catch {
        /* 未登录或接口不可用：保持 null，本段整体不渲染 */
      }
    }
    void load()
    return () => { alive = false }
  }, [enabled])
  return data
}

function clusterSkillNames(cluster: RoleContractCluster): string[] {
  return (cluster.skills || [])
    .map(skill => (typeof skill === 'string' ? skill : skill?.name))
    .filter((name): name is string => !!name)
}

function StatBar({ stats, state }: { stats: PublicStats | null; state: LoadState }) {
  if (state === 'unavailable') return null
  const coverage = stats?.identified_employer_coverage
  const items = [
    { label: '在库岗位', value: stats?.total_jobs, unit: '个' },
    { label: '支撑 JD', value: stats?.total_jds, unit: '条' },
    { label: '能力技能点', value: stats?.total_skills, unit: '个' },
    {
      label: '雇主识别覆盖率',
      value: coverage == null ? undefined : Math.round((coverage <= 1 ? coverage * 100 : coverage)),
      unit: '%',
    },
  ]
  return (
    <div className="tg-topbar grid grid-cols-1 gap-px overflow-hidden rounded-2xl border border-line-soft/6 bg-line-soft/8 shadow-[0_10px_30px_-18px_rgb(var(--brand-ink)/0.22)] sm:grid-cols-2 lg:grid-cols-4">
      {items.map(item => (
        <div key={item.label} className="bg-white px-5 py-5 sm:px-6">
          {/* 用 text-body-2 而不是 .label 的 text-body-3：#94A0BF 在白卡上只有 2.6:1，
              12px 的四个标签正是本轮被点名「必须可见」的元素，text-body-2 (#5A678C) 是 5.6:1。 */}
          <div className="text-xs font-semibold text-body-2">{item.label}</div>
          {state === 'loading' || item.value == null ? (
            <div className="mt-2 h-8 w-24 animate-pulse rounded-xl bg-brand-ink/8" aria-label="数据加载中" />
          ) : (
            <div className="mt-1.5 flex items-baseline gap-1">
              <span className="text-3xl font-extrabold tabular-nums text-accent-deep">{item.value}</span>
              <span className="text-sm font-semibold text-body-3">{item.unit}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function FlowBand() {
  return (
    <>
      {/* 桌面：一条带箭头的时间轴，主干节点更大更重 */}
      <ol className="relative mt-12 hidden grid-cols-6 gap-4 lg:grid">
        <div aria-hidden
          className="absolute left-8 right-8 top-12 h-px bg-gradient-to-r from-transparent via-brand-ink/20 to-transparent" />
        {FLOW.map(({ icon: Icon, title, text, primary }, index) => (
          <li key={title} className="relative flex min-w-0 flex-col items-center text-center">
            <div className="grid h-24 place-items-center">
              <span className={primary
                ? 'relative z-10 grid h-[74px] w-[74px] place-items-center rounded-2xl bg-grad-accent text-white shadow-[0_14px_30px_-14px_rgb(var(--brand-accent)/0.55)]'
                : 'relative z-10 grid h-14 w-14 place-items-center rounded-2xl border border-line-soft/14 bg-white text-accent shadow-[0_8px_20px_-14px_rgb(var(--brand-ink)/0.4)]'}>
                <Icon className={primary ? 'h-7 w-7' : 'h-5 w-5'} />
              </span>
            </div>
            {index < FLOW.length - 1 && (
              <ChevronRight aria-hidden
                className="absolute right-[-11px] top-[38px] z-10 h-4 w-4 text-brand-ink/30" />
            )}
            <div className="mt-1 flex items-center gap-1.5 text-[11px] font-bold tabular-nums text-body-3">
              <span>0{index + 1}</span>
              {primary && <span className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent-deep">主干</span>}
            </div>
            <h3 className={`mt-2 font-bold text-body-1 ${primary ? 'text-base' : 'text-sm'}`}>{title}</h3>
            <p className="mt-1.5 text-xs leading-5 text-body-2">{text}</p>
          </li>
        ))}
      </ol>

      {/* 移动：纵向步进，左侧一条连贯的轴 */}
      <ol className="relative mt-8 space-y-3 pl-14 lg:hidden">
        <div aria-hidden className="absolute left-[21px] top-3 bottom-3 w-px bg-brand-ink/14" />
        {FLOW.map(({ icon: Icon, title, text, primary }, index) => (
          <li key={title} className="relative">
            <span className={`absolute -left-14 top-1 grid h-[42px] w-[42px] place-items-center rounded-2xl ${primary
              ? 'bg-grad-accent text-white shadow-[0_10px_22px_-14px_rgb(var(--brand-accent)/0.6)]'
              : 'border border-line-soft/14 bg-white text-accent'}`}>
              <Icon className="h-[18px] w-[18px]" />
            </span>
            <div className="rounded-2xl border border-line-soft/10 bg-white px-4 py-3.5 shadow-[0_8px_24px_-20px_rgb(var(--brand-ink)/0.35)]">
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] font-bold tabular-nums text-body-3">0{index + 1}</span>
                <h3 className="text-sm font-bold text-body-1">{title}</h3>
              </div>
              <p className="mt-1 text-xs leading-5 text-body-2">{text}</p>
            </div>
          </li>
        ))}
      </ol>
    </>
  )
}

function EvidenceSection({ data }: { data: Showcase }) {
  return (
    <section className="section-pad border-t border-line-soft/10 bg-white" aria-labelledby="evidence-title">
      <div className="section-wrap">
        <div className="max-w-3xl">
          <div className="eyebrow">VERIFIABLE EVIDENCE</div>
          <h2 id="evidence-title" className="mt-3 text-3xl font-extrabold leading-tight text-body-1 sm:text-4xl">
            每一条能力要求，都能点回它的原始出处
          </h2>
          <p className="mt-5 text-sm leading-7 text-body-2 sm:text-base">
            下面是系统中语料最厚的一个真实岗位。左侧是它经交叉验证后的能力簇，右侧是支撑这些能力的公开招聘信息原始链接——都可以直接点开核对。
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0 rounded-2xl border border-line-soft/12 bg-surface-muted p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-lg font-extrabold text-body-1">{data.job.name}</span>
              <span className="chip border border-accent/25 bg-accent/8 text-accent">v{data.job.version} 当前版本</span>
              {!!data.job.employer_count && (
                <span className="chip border border-line-soft/14 bg-white text-body-2">{data.job.employer_count} 家独立雇主</span>
              )}
            </div>
            <div className="mt-1 text-xs text-body-3">{data.job.category}</div>
            <ul className="mt-5 space-y-3">
              {data.clusters.map(cluster => (
                <li key={cluster.name} className="rounded-2xl border border-line-soft/10 bg-white px-4 py-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 text-sm font-bold text-body-1">{cluster.name}</span>
                    <span className={`chip shrink-0 border ${cluster.importance === 'required'
                      ? 'border-brand-ink/15 bg-brand-ink/6 text-brand-ink'
                      : 'border-line-soft/12 bg-surface-muted text-body-2'}`}>
                      {cluster.importance === 'required' ? '必备' : '加分'}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {clusterSkillNames(cluster).slice(0, 5).map(name => (
                      <span key={name} className="chip border border-line-soft/10 bg-surface-muted text-body-2">{name}</span>
                    ))}
                  </div>
                  <div className="mt-2.5 text-[11px] text-body-3">
                    置信 {Math.round((cluster.confidence || 0) * 100)}%
                    {!!cluster.employer_count && ` · ${cluster.employer_count} 家雇主交叉验证`}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="min-w-0 rounded-2xl border border-line-soft/12 bg-white p-5 sm:p-6">
            <div className="flex items-center gap-2 text-sm font-bold text-body-1">
              <ShieldCheck className="h-4 w-4 text-accent" />原始证据链接
            </div>
            <ul className="mt-4 space-y-2.5">
              {data.evidences.map(item => (
                <li key={item.url}>
                  <a href={item.url} target="_blank" rel="noreferrer noopener"
                    className="group flex min-w-0 items-start gap-3 rounded-2xl border border-line-soft/10 bg-surface-muted px-4 py-3 transition hover:border-accent/40 hover:bg-white">
                    <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-body-3 transition group-hover:text-accent" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-body-1">
                        {item.company || item.source || '公开招聘信息'}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-body-3">
                        {[item.skill, item.source, item.publishDate].filter(Boolean).join(' · ')}
                      </span>
                      <span className="mt-1 block truncate text-[11px] text-accent">{item.url}</span>
                    </span>
                  </a>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-[11px] leading-5 text-body-3">
              证据来自公开招聘平台，采集遵循 robots 与频率约束；能力项需 ≥2 家独立雇主支撑才会转为已验证状态。
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

export default function Portal() {
  const { user, ready } = useAuth()
  const workspace = user ? roleHome(user.role) : '/login'
  // 等会话恢复完成再发请求：早发会让已登录用户带不上 token，白白吃一个 401 被登出
  const { stats, state } = usePortalStats(ready)
  // 证据段要读岗位明细，仍然需要登录；匿名访客不发这个注定 401 的请求，整段不渲染
  const showcase = useEvidenceShowcase(ready && !!user)

  return (
    <div className="portal-page min-h-screen overflow-x-hidden bg-surface-page text-body-1">
      <section className="relative overflow-hidden border-b border-line-soft/10">
        <img src="/portal-hero.webp" alt=""
          className="absolute inset-0 h-full w-full object-cover object-center"
          onError={event => { event.currentTarget.hidden = true }} />
        {/* 遮罩降到 0.86→0.48：此前是 0.99→0.82，白得几乎全不透明，/portal-hero.webp 等于没贴 */}
        <div className="absolute inset-0 bg-[linear-gradient(100deg,rgb(var(--surface-page)/0.86)_0%,rgb(var(--surface-page)/0.78)_40%,rgb(var(--surface-page)/0.62)_70%,rgb(var(--surface-page)/0.48)_100%)]" />

        <header className="relative z-10 section-wrap flex h-20 items-center !max-w-[1320px]">
          <Link to="/" className="flex min-w-0 items-center gap-3" aria-label="智岗图谱首页">
            <img src="/logo.png" alt="" className="h-10 w-10 shrink-0 rounded-xl border border-line-soft/12 bg-white object-cover shadow-sm" />
            <span className="hidden min-w-0 sm:block">
              <span className="block truncate text-lg font-bold text-body-1">智岗图谱</span>
              <span className="block truncate text-[11px] text-body-3">TalentGraph AI</span>
            </span>
          </Link>
          <nav className="ml-auto flex items-center gap-2" aria-label="公共导航">
            {!user && <Link to="/login" title="登录" aria-label="登录" className="portal-nav-link"><LogIn className="h-4 w-4" /><span className="hidden md:inline">登录</span></Link>}
            {!user && <Link to="/register" title="注册" aria-label="注册" className="portal-nav-link"><UserPlus className="h-4 w-4" /><span className="hidden md:inline">注册</span></Link>}
            <Link to={workspace} title="进入工作台"
              className="btn-primary h-10 w-10 !px-0 sm:w-auto sm:!px-5">
              <span className="hidden sm:inline">进入工作台</span><ArrowRight className="h-4 w-4" />
            </Link>
          </nav>
        </header>

        {/* 非对称构图：左文案权重更高，右侧真实产品截图 */}
        <div className="relative z-10 section-wrap grid items-center gap-12 pb-16 pt-10 sm:pt-12 lg:grid-cols-[minmax(0,1.02fr)_minmax(0,0.98fr)] lg:gap-10 lg:pb-24 lg:pt-16">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: 'easeOut' }} className="min-w-0">
            <div className="pill-badge mb-7">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-accent" />面向新一代信息技术产业的人才能力基础设施
            </div>
            {/* 参考稿的两行标题：第一行近黑，第二行青→紫渐变。
                字号按「第二行 8 个字必须单行放下」定：lg 64px×8=512px < 左栏约 590px，
                放大到 72px 会把「证」挤成孤字第三行。 */}
            <h1 className="text-[2.5rem] font-extrabold leading-[1.12] tracking-tight sm:text-[3.5rem] lg:text-[4rem]">
              <span className="block text-body-1">智岗图谱</span>
              <span className="block gradient-text">让岗位能力可验证</span>
            </h1>
            <p className="mt-7 max-w-xl text-base leading-8 text-body-2">
              多源异构证据驱动的岗位能力图谱与动态演化分析。从真实岗位证据出发，连接能力图谱、岗位演化与人岗诊断，让每一次人才决策都有依据、有版本、可复核。
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link to={workspace} className="btn-primary px-6 py-3 text-base">
                {user ? '返回我的工作台' : '前往工作台'}<ArrowRight className="h-[18px] w-[18px]" />
              </Link>
              {!user && <Link to="/register" className="btn-ghost px-6 py-3 text-base">创建账号</Link>}
            </div>
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.12, ease: 'easeOut' }}
            className="relative min-w-0">
            <div className="overflow-hidden rounded-2xl border border-white/80 bg-white/80 p-2 shadow-[0_28px_60px_-28px_rgb(var(--brand-ink)/0.45)] backdrop-blur-sm">
              <img src="/shot-dashboard.webp" alt="智岗图谱数据驾驶舱界面：岗位、能力与置信度指标总览"
                width={1280} height={800} loading="eager"
                className="block w-full rounded-xl"
                onError={event => { event.currentTarget.hidden = true }} />
            </div>
            <div className="mt-3 flex items-center gap-2 text-[11px] text-body-3">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />系统真实运行界面 · 数据驾驶舱
            </div>
          </motion.div>
        </div>
      </section>

      {/* 真实数据条：接口拿不到就整条不渲染，不出现写死数字。
          relative z-10 是必须的：本节是静态定位，负 margin 把它拉进了上面那个 relative
          英雄区的覆盖带，英雄区的背景图与遮罩两层 absolute inset-0 会盖住整条 KPI。 */}
      {state !== 'unavailable' && (
        <section className="section-wrap relative z-10 -mt-10 pb-2 sm:-mt-12" aria-label="平台实时数据">
          <StatBar stats={stats} state={state} />
        </section>
      )}

      <section className="section-wrap py-14 sm:py-16" aria-label="系统原则">
        <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
          {PRINCIPLES.map(({ icon: Icon, title, text }) => (
            <div key={title} className="tg-topbar overflow-hidden rounded-2xl border border-line-soft/6 bg-white p-6 shadow-[0_10px_30px_-20px_rgb(var(--brand-ink)/0.24)]">
              <span className="grid h-10 w-10 place-items-center rounded-2xl bg-grad-accent text-white shadow-glow"><Icon className="h-5 w-5" /></span>
              <h2 className="mt-4 text-base font-bold text-body-1">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-body-2">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="relative overflow-hidden border-y border-line-soft/10 bg-surface-muted section-pad" aria-labelledby="loop-title">
        <img src="/project-loop.webp" alt="" aria-hidden
          className="pointer-events-none absolute -right-24 top-0 h-full w-[52%] object-cover opacity-[0.14]"
          onError={event => { event.currentTarget.hidden = true }} />
        <div className="relative section-wrap">
          <div className="max-w-3xl">
            <div className="eyebrow">PROJECT LOOP</div>
            <h2 id="loop-title" className="mt-3 text-3xl font-extrabold leading-tight text-body-1 sm:text-4xl">从公开证据到人才行动，形成可验证闭环</h2>
            <p className="mt-5 text-sm leading-7 text-body-2 sm:text-base">每个环节保留输入、处理状态和输出记录，反馈只有在关联真实证据或岗位版本后才进入下一轮重算。</p>
          </div>
          <FlowBand />
          <div className="mt-10 flex items-center gap-2 text-xs text-body-2">
            <RefreshCcw className="h-4 w-4 text-accent" />反馈回流后重新进入数据治理与证据核验
          </div>
        </div>
      </section>

      {showcase && <EvidenceSection data={showcase} />}

      <footer className="border-t border-line-soft/10 bg-white text-body-2">
        <div className="section-wrap flex flex-col gap-4 py-8 md:flex-row md:items-center">
          <div className="flex items-center gap-2 font-semibold text-body-1"><BriefcaseBusiness className="h-4 w-4 text-accent" />智岗图谱</div>
          <p className="text-xs text-body-3 md:ml-auto">多源证据 · 知识图谱 · 动态演化 · 人岗诊断</p>
        </div>
      </footer>
    </div>
  )
}
