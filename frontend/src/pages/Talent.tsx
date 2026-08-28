import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Upload, Loader2, ShieldCheck, ExternalLink, Plus, Trash2, History } from 'lucide-react'
import {
  IUsersThree, IDatabase, ITarget, IShieldCheck, IUser, IStack, ITrendUp, ILightbulb,
} from '../components/icons'
import {
  api, errMsg, JobListItem, TalentCorpus, TalentProfile, SupplyDemand,
  TeamItem, TeamGap, AliasItem,
  TeamEvent,
} from '../api'
import { Card, Badge, Spinner, EmptyState, ErrorState, Meter } from '../components/ui'
import { Kpi } from '../components/Kpi'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useAuth } from '../auth'

type Tab = 'team' | 'supply' | 'corpus' | 'alias'

const TABS = [
  { key: 'team', label: '团队能力盘点', icon: IUsersThree },
  { key: 'supply', label: '供需缺口对照', icon: ITrendUp },
  { key: 'corpus', label: '简历语料台账', icon: IDatabase },
  { key: 'alias', label: '学到的技能表述', icon: ILightbulb },
] as const

const SRC_LABEL: Record<string, string> = {
  dataset: '公开数据集', web: '公开个人简历', sample: '公开范文', upload: '团队上传',
}

export default function Talent() {
  const [tab, setTab] = useState<Tab>('team')
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [corpus, setCorpus] = useState<TalentCorpus | null>(null)
  const [error, setError] = useState(false)

  const load = () => {
    setError(false)
    Promise.all([api.jobs({ size: 100 }), api.talentCorpus()])
      .then(([j, c]) => { setJobs(j.items); setCorpus(c) })
      .catch(() => setError(true))
  }
  useEffect(() => { load() }, [])

  if (error) return <ErrorState onRetry={load} />
  if (!corpus) return <Spinner label="加载人才画像…" />

  return (
    <div className="space-y-6">
      {/* Hero：纯 CSS 渐变光晕，public/ 无人才主题配图。
          用裸 div.glass 而非 Card（同 Dashboard），避免多叠一层层叠上下文 */}
      <div className="relative overflow-hidden rounded-2xl glass">
        <div aria-hidden className="absolute inset-0 pointer-events-none bg-gradient-to-br from-sky-50/70 via-transparent to-accent/8/50" />
        <div aria-hidden className="absolute -top-20 -right-12 w-64 h-64 rounded-full blur-3xl opacity-[0.18] bg-grad-accent ring-1 ring-accent/20" />
        <div aria-hidden className="absolute -bottom-24 -left-16 w-56 h-56 rounded-full blur-3xl opacity-[0.14] bg-grad-accent ring-1 ring-accent/20" />

        <div className="relative z-10 px-5 py-6 sm:px-8 sm:py-9">
          <div className="flex items-start gap-4">
            <div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow shrink-0">
              <IUsersThree className="w-6 h-6 text-accent-deep" />
            </div>
            <div className="min-w-0">
              <h1 className="text-2xl font-extrabold text-slate-900 sm:text-3xl">人才与团队盘点</h1>
              <p className="text-slate-500 mt-1 max-w-2xl">
                脱敏人才画像驱动的团队能力盘点 · 供需缺口对照 · 学到的技能表述
              </p>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-baseline gap-1.5 rounded-full bg-white/70 border border-white/80 px-3 py-1.5 text-xs text-slate-500 shadow-sm">
              <b className="text-sm font-extrabold text-slate-900 tabular-nums">{corpus.total_profiles}</b>份脱敏人才画像
            </span>
            <span className="inline-flex items-baseline gap-1.5 rounded-full bg-white/70 border border-white/80 px-3 py-1.5 text-xs text-slate-500 shadow-sm">
              <b className="text-sm font-extrabold text-slate-900 tabular-nums">{corpus.total_skills_extracted}</b>项已抽取技能
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/8/80 border border-accent/20 px-3 py-1.5 text-xs font-medium text-accent-deep">
              <IShieldCheck className="w-3.5 h-3.5" />简历原文与身份信息不入库
            </span>
          </div>
        </div>
      </div>

      {/* 未选中项不用 btn-ghost：它自带 border-slate-200，在导轨内会形成双重描边 */}
      <div className="flex flex-wrap items-center gap-1.5 p-1.5 rounded-2xl bg-white/60 border border-white/80 backdrop-blur-xl shadow-card sm:inline-flex">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium whitespace-nowrap transition ${
              tab === t.key ? 'bg-grad-accent ring-1 ring-accent/20 text-accent-deep shadow-glow'
                : 'text-slate-600 hover:bg-white hover:text-slate-900'}`}>
            <t.icon className="w-4 h-4" />{t.label}
          </button>
        ))}
      </div>

      {tab === 'team' && <TeamPanel jobs={jobs} onChanged={load} />}
      {tab === 'supply' && <SupplyPanel jobs={jobs} />}
      {tab === 'corpus' && <CorpusPanel corpus={corpus} />}
      {tab === 'alias' && <AliasPanel corpus={corpus} />}
    </div>
  )
}

/* ---------------- 团队能力盘点：谁能补 / 还缺谁 ---------------- */
function TeamPanel({ jobs, onChanged }: { jobs: JobListItem[]; onChanged: () => void }) {
  const [teams, setTeams] = useState<TeamItem[]>([])
  const [teamId, setTeamId] = useState<number | null>(null)
  const [jobId, setJobId] = useState<number | null>(null)
  const [gap, setGap] = useState<TeamGap | null>(null)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [authorized, setAuthorized] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')
  const [teamEvents, setTeamEvents] = useState<TeamEvent[]>([])
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const auth = useAuth()
  const canManageTeam = auth.can('hr')

  useEffect(() => {
    api.teams().then(d => { setTeams(d.items); if (d.items[0]) setTeamId(d.items[0].id) })
      .catch(() => {})
  }, [])
  useEffect(() => { if (jobs[0] && !jobId) setJobId(jobs[0].id) }, [jobs])
  useEffect(() => {
    if (!teamId || !jobId) return
    setLoading(true)
    api.teamGap(teamId, jobId).then(setGap).catch(() => setGap(null)).finally(() => setLoading(false))
  }, [teamId, jobId])
  useEffect(() => {
    if (!teamId || !canManageTeam) { setTeamEvents([]); return }
    api.teamHistory(teamId).then(d => setTeamEvents(d.items || [])).catch(() => setTeamEvents([]))
  }, [teamId, canManageTeam])

  const reloadTeam = async (selectedId = teamId) => {
    const d = await api.teams()
    setTeams(d.items)
    if (selectedId) setTeamId(selectedId)
    if (selectedId && jobId) {
      api.teamGap(selectedId, jobId).then(setGap).catch(() => setGap(null))
      api.teamHistory(selectedId).then(r => setTeamEvents(r.items || [])).catch(() => {})
    }
    onChanged()
  }

  const createTeam = async () => {
    if (!newTeamName.trim() || !jobId) { toast('error', '请输入团队名称并选择目标岗位'); return }
    try {
      const team = await api.createTeam({ name: newTeamName.trim(), target_job_id: jobId })
      setNewTeamName('')
      await reloadTeam(team.id)
      toast('success', `团队“${team.name}”已创建`)
    } catch (error) { toast('error', errMsg(error, '团队创建失败')) }
  }

  const removeMember = async (memberId: number, displayName: string) => {
    if (!teamId || !window.confirm(`确认将“${displayName}”移出团队？`)) return
    try {
      const result = await api.removeTeamMember(teamId, memberId)
      await reloadTeam(teamId)
      const before = Math.round(Number(result.before?.coverage_rate || 0) * 100)
      const after = Math.round(Number(result.after?.coverage_rate || 0) * 100)
      toast('success', `成员已移出，覆盖率 ${before}% → ${after}%`)
    } catch (error) { toast('error', errMsg(error, '移出成员失败')) }
  }

  const onFile = async (f: File) => {
    if (!teamId) return
    if (f.name.toLowerCase().endsWith('.doc')) { toast('error', '旧版 .doc 不支持，请转为 DOCX'); return }
    setUploading(true)
    try {
      const r = await api.uploadTeamResume(teamId, f, '新成员', '', '', authorized, 90)
      toast('success', `已加入团队：${r.code}，提取 ${r.skill_count} 项技能（原文未留存）`)
      setAuthorized(false)
      await reloadTeam(teamId)
    } catch (e: any) { toast('error', errMsg(e, '简历解析失败，请确认为 PDF/Word 格式')) }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = '' }
  }

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <div className="flex-1 min-w-0">
            <div className="label mb-1.5">团队</div>
            <Select value={String(teamId ?? '')} onChange={v => setTeamId(Number(v))}
              options={teams.map(t => ({ value: String(t.id), label: `${t.name}（${t.size} 人）` }))} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="label mb-1.5">目标岗位</div>
            <Select value={String(jobId ?? '')} onChange={v => setJobId(Number(v))}
              options={jobs.map(j => ({ value: String(j.id), label: j.name }))} />
          </div>
          {canManageTeam && (
            <div className="shrink-0 w-full sm:w-auto">
              <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" className="hidden"
                onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
              <button onClick={() => fileRef.current?.click()} disabled={uploading || !teamId || !authorized}
                className="btn-primary w-full sm:w-auto whitespace-nowrap">
                {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                加入成员简历
              </button>
            </div>
          )}
        </div>
        {canManageTeam && (
          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
            <div className="flex gap-2">
              <input aria-label="新团队名称" value={newTeamName} onChange={e => setNewTeamName(e.target.value)}
                className="input" placeholder="新团队名称" maxLength={64} />
              <button onClick={() => void createTeam()} className="btn-ghost whitespace-nowrap">
                <Plus className="w-4 h-4" /> 创建团队
              </button>
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer">
              <input type="checkbox" checked={authorized} onChange={e => setAuthorized(e.target.checked)} />
              已获得成员简历处理授权
            </label>
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500 flex items-start gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 mt-0.5 shrink-0 text-accent-deep" />
          {canManageTeam
            ? '组织私有人才数据可在公共图谱只读模式下追加。简历只在内存中解析，服务端仅留存脱敏技能要素。'
            : '当前为公共演示视图；登录 HR 组织账号后可导入授权成员并查看加入前后的覆盖变化。'}
        </p>
      </Card>

      {loading && <Spinner label="计算团队能力缺口…" />}
      {!loading && !gap && <EmptyState text="暂无盘点结果" hint="请选择团队与目标岗位" />}
      {!loading && gap && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Kpi delay={0} label="团队人数" value={gap.team.size} unit="人" sub={gap.team.name}
              icon={<IUsersThree className="w-5 h-5 text-accent-deep" />} tone="bg-grad-accent ring-1 ring-accent/20" />
            <Kpi delay={0.05} label="必备能力覆盖" value={`${gap.required_covered}/${gap.required_total}`}
              sub={`覆盖率 ${(gap.coverage_rate * 100).toFixed(0)}%`}
              icon={<ITarget className="w-5 h-5 text-accent-deep" />} tone="bg-grad-violet" />
            <Kpi delay={0.1} label="加权覆盖率" value={gap.weighted_coverage * 100} unit="%" decimals={1}
              sub="按岗位能力权重加权" ring={gap.weighted_coverage} />
            <Kpi delay={0.15} label="加分能力覆盖" value={`${gap.bonus_covered}/${gap.bonus_total}`} sub="加分项"
              icon={<IStack className="w-5 h-5 text-accent-deep" />} tone="bg-accent/80" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card className="p-5" delay={0.05}>
              <div className="flex items-center justify-between gap-2 mb-3">
                <h3 className="font-semibold text-slate-800 flex items-center gap-2 min-w-0">
                  <span className="w-6 h-6 rounded-lg bg-rose-50 border border-rose-100 grid place-items-center shrink-0">
                    <ITarget className="w-3.5 h-3.5 text-rose-500" />
                  </span>
                  <span className="shrink-0">还缺谁</span>
                  <span className="text-xs font-normal text-slate-400 truncate">团队没人具备的必备能力</span>
                </h3>
                {/* shrink-0 + nowrap：1024px 两栏时卡片最窄，否则标题会断成「还缺 / 谁」、徽章文字也会折行 */}
                {gap.missing.length > 0 && (
                  <span className="shrink-0 whitespace-nowrap">
                    <Badge tone="rose">{gap.missing.length} 项 · 按权重降序</Badge>
                  </span>
                )}
              </div>
              {gap.missing.length === 0
                ? <EmptyState text="该岗位的必备能力已全部覆盖" />
                : <div className="space-y-2 max-h-[300px] overflow-y-auto -mr-2 pr-2 sm:max-h-[420px]">
                    {gap.missing.map(m => (
                      <div key={m.skill} className="rounded-xl bg-sky-50/70 border border-slate-200/70 px-3.5 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-slate-800 truncate" title={m.skill}>{m.skill}</span>
                          <span className="text-xs font-semibold text-rose-600 tabular-nums shrink-0">
                            {m.weight.toFixed(2)}
                          </span>
                        </div>
                        {/* rose 不再铺满整行底色，只作为权重的语义编码留在数字与条上 */}
                        <div className="mt-1.5">
                          <Meter value={m.weight} tone="bg-gradient-to-r from-rose-400 to-amber-400" min={4} />
                        </div>
                      </div>
                    ))}
                  </div>}
            </Card>

            <Card className="p-5" delay={0.1}>
              <div className="flex items-center justify-between gap-2 mb-3">
                <h3 className="font-semibold text-slate-800 flex items-center gap-2 min-w-0">
                  <span className="w-6 h-6 rounded-lg bg-indigo-50 border border-indigo-100 grid place-items-center shrink-0">
                    <IUser className="w-3.5 h-3.5 text-indigo-500" />
                  </span>
                  <span className="shrink-0">谁能补</span>
                  <span className="text-xs font-normal text-slate-400 truncate">成员对必备能力的贡献</span>
                </h3>
              </div>
              {gap.contributions.length === 0
                ? <EmptyState text="团队暂无成员" hint="上传成员简历后自动生成贡献分析" />
                : <div className="space-y-2 max-h-[300px] overflow-y-auto -mr-2 pr-2 sm:max-h-[420px]">
                    {gap.contributions.map(c => {
                      const pct = gap.required_total ? c.covers_required / gap.required_total : 0
                      return (
                        <div key={c.member_id} className="rounded-xl bg-sky-50/70 border border-slate-200/70 px-3.5 py-2.5">
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-2 min-w-0">
                              <span className="w-7 h-7 rounded-lg bg-grad-violet grid place-items-center shrink-0
                                               text-[11px] font-bold text-accent-deep">
                                {c.display_name.slice(0, 1)}
                              </span>
                              <span className="text-sm font-medium text-slate-800 truncate">
                                {c.display_name}
                                <span className="ml-1.5 font-mono text-[11px] font-normal text-slate-400">{c.talent_code}</span>
                              </span>
                            </span>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <Badge tone="indigo">覆盖 {c.covers_required}</Badge>
                              {c.uniquely_covers > 0 && <Badge tone="amber">独有 {c.uniquely_covers}</Badge>}
                              {canManageTeam && <button onClick={() => void removeMember(c.member_id, c.display_name)}
                                className="icon-btn !w-7 !h-7 text-rose-500" title="移出团队" aria-label={`移出 ${c.display_name}`}>
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>}
                            </div>
                          </div>
                          <div className="mt-2 flex items-center gap-2">
                            <div className="flex-1"><Meter value={pct} /></div>
                            <span className="w-10 shrink-0 text-right text-[11px] text-slate-500 tabular-nums">
                              {Math.round(pct * 100)}%
                            </span>
                          </div>
                          <div className="mt-1.5 text-xs text-slate-500 truncate">
                            {c.role_label || '—'} · 技能 {c.skill_count} 项
                            {c.unique_skills.length > 0 && <> · 仅他会：{c.unique_skills.slice(0, 3).join('、')}</>}
                          </div>
                        </div>
                      )
                    })}
                  </div>}
            </Card>
          </div>
          {canManageTeam && (
            <Card className="p-5" delay={0.12}>
              <h3 className="font-semibold text-slate-800 flex items-center gap-2 mb-3">
                <History className="w-4 h-4 text-indigo-500" /> 团队变化历史
              </h3>
              {teamEvents.length === 0 ? <EmptyState text="暂无团队变更" /> : (
                <div className="divide-y divide-slate-100">
                  {teamEvents.slice(0, 10).map(event => {
                    const labels: Record<string, string> = { created: '创建团队', member_added: '加入成员', member_uploaded: '上传成员', member_removed: '移出成员' }
                    const before = event.before?.coverage_rate
                    const after = event.after?.coverage_rate
                    return <div key={event.id} className="py-2.5 flex items-center justify-between gap-3 text-sm">
                      <div className="min-w-0"><b className="text-slate-700">{labels[event.action] || event.action}</b>
                        <span className="ml-2 text-slate-500">{event.details?.display_name || event.details?.code || ''}</span></div>
                      <div className="shrink-0 text-xs text-slate-400">
                        {before != null && after != null && <span className="mr-3">覆盖率 {Math.round(before * 100)}% → {Math.round(after * 100)}%</span>}
                        {new Date(event.created_at).toLocaleString('zh-CN')}
                      </div>
                    </div>
                  })}
                </div>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  )
}

/* ---------------- 供需缺口对照 ---------------- */
function SupplyPanel({ jobs }: { jobs: JobListItem[] }) {
  const [jobId, setJobId] = useState<number | null>(null)
  const [data, setData] = useState<SupplyDemand | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { if (jobs[0] && !jobId) setJobId(jobs[0].id) }, [jobs])
  useEffect(() => {
    if (!jobId) return
    setLoading(true)
    api.supplyDemand(jobId).then(setData).catch(() => setData(null)).finally(() => setLoading(false))
  }, [jobId])

  const option = useMemo(() => {
    if (!data) return {}
    const top = data.items.slice(0, 12).reverse()
    return {
      grid: { left: 8, right: 24, top: 30, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['岗位需求权重', '人才供给率'], top: 0, textStyle: { fontSize: 11 } },
      xAxis: { type: 'value', max: 1, axisLabel: { formatter: (v: number) => `${v * 100}%` } },
      yAxis: { type: 'category', data: top.map(i => i.skill), axisLabel: { fontSize: 11 } },
      series: [
        { name: '岗位需求权重', type: 'bar', data: top.map(i => i.weight),
          itemStyle: { color: '#6366F1', borderRadius: [0, 4, 4, 0] }, barGap: 0 },
        { name: '人才供给率', type: 'bar', data: top.map(i => i.supply_rate),
          itemStyle: { color: '#0EA5E9', borderRadius: [0, 4, 4, 0] } },
      ],
    }
  }, [data])

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <div className="flex-1 min-w-0">
            <div className="label mb-1.5">目标岗位</div>
            <Select value={String(jobId ?? '')} onChange={v => setJobId(Number(v))}
              options={jobs.map(j => ({ value: String(j.id), label: j.name }))} />
          </div>
          {data && (
            <div className="text-xs text-slate-500 sm:pb-2.5">
              对照 <b className="text-slate-700 tabular-nums">{data.corpus_size}</b> 份脱敏画像
            </div>
          )}
        </div>
      </Card>
      {loading && <Spinner label="计算供需缺口…" />}
      {!loading && data && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Kpi delay={0} label="语料人数" value={data.corpus_size} unit="人" sub="脱敏人才画像总量"
              icon={<IDatabase className="w-5 h-5 text-accent-deep" />} tone="bg-grad-accent ring-1 ring-accent/20" />
            <Kpi delay={0.05} label="对口人才" value={data.aligned_talents} unit="人" sub="映射到该岗位"
              icon={<IUser className="w-5 h-5 text-accent-deep" />} tone="bg-grad-violet" />
            <Kpi delay={0.1} label="必备能力被覆盖" value={`${data.required_covered}/${data.required_total}`}
              sub="语料中至少 1 人具备"
              icon={<ITarget className="w-5 h-5 text-accent-deep" />} tone="bg-accent/80" />
            <Kpi delay={0.15} label="覆盖率" value={data.coverage_rate * 100} unit="%" decimals={1}
              sub="人才供给对岗位需求" ring={data.coverage_rate} />
          </div>
          <Card className="p-5" delay={0.05}>
            <h3 className="font-semibold text-slate-800 mb-1">需求权重 vs 人才供给率（缺口最大的前 12 项）</h3>
            <p className="text-xs text-slate-500 mb-3">{data.note}</p>
            <ReactECharts option={option} style={{ height: 420 }} notMerge />
          </Card>
        </>
      )}
    </div>
  )
}

/* ---------------- 语料台账 ---------------- */
function CorpusPanel({ corpus }: { corpus: TalentCorpus }) {
  const [profiles, setProfiles] = useState<TalentProfile[]>([])
  const [src, setSrc] = useState('')

  useEffect(() => {
    api.talentProfiles({ size: 100, source_type: src || undefined })
      .then(d => setProfiles(d.items)).catch(() => setProfiles([]))
  }, [src])

  return (
    <div className="space-y-4">
      {/* 内层横幅不再自带描边：套在 glass 卡里会形成双重边框 */}
      <Card className="p-4">
        <div className="flex items-start gap-2.5 text-sm text-accent-deep">
          <span className="w-8 h-8 rounded-lg bg-accent/8 border border-accent/20 grid place-items-center shrink-0">
            <IShieldCheck className="w-4 h-4 text-accent-deep" />
          </span>
          <span className="leading-relaxed pt-1.5">{corpus.privacy_notice}</span>
        </div>
      </Card>

      <Card className="p-5" delay={0.05}>
        <h3 className="font-semibold text-slate-800 mb-3">采集批次与许可证</h3>
        {/* 负边距要与卡片 p-5 一致，否则横向滚动条与内容左边缘对不齐 */}
        <div className="overflow-x-auto -mx-5 px-5">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-slate-100">
                <th className="py-2 pr-3">批次</th><th className="py-2 pr-3">类型</th>
                <th className="py-2 pr-3">来源</th><th className="py-2 pr-3">许可/依据</th>
                <th className="py-2 pr-3">robots 遵循</th><th className="py-2 pr-3">入库</th>
              </tr>
            </thead>
            <tbody>
              {corpus.batches.map(b => (
                <tr key={b.batch_key} className="border-b border-slate-50">
                  <td className="py-2 pr-3 font-mono text-xs">{b.batch_key}</td>
                  <td className="py-2 pr-3"><Badge tone="indigo">{SRC_LABEL[b.source_type] || b.source_type}</Badge></td>
                  <td className="py-2 pr-3 max-w-[200px] truncate" title={b.source_name}>{b.source_name}</td>
                  <td className="py-2 pr-3 max-w-[260px] truncate" title={b.license}>{b.license}</td>
                  {/* robots_ok = 本批次「零请求被 robots 拦下」。为 false 不是"违反 robots"，
                      恰恰相反：说明有 URL 被 robots 拦下、采集器照规矩跳过了。原先显示
                      ⚠️ 会被读成合规有问题，与事实相反，故改为两种情况都写明"遵循"。 */}
                  <td className="py-2 pr-3 whitespace-nowrap"
                      title={b.robots_ok
                        ? '本批次逐 host 检查 robots.txt，无请求被拦下'
                        : '本批次逐 host 检查 robots.txt，有 URL 被 Disallow，采集器已跳过、未采集'}>
                    {b.robots_ok
                      ? '✅ 遵循'
                      : <>✅ 遵循<span className="ml-1 text-xs text-slate-400">（有拦截已跳过）</span></>}
                  </td>
                  <td className="py-2 pr-3 tabular-nums" title={`采集 ${b.collected} 份，清洗后留 ${b.kept} 份，跨批次近重复剔除后实际入库 ${b.profiles} 份`}>
                    {b.profiles}
                    {/* 不写"留 N"：本页另一个页签有"留出集"，"留"字会被读成 holdout。 */}
                    {b.profiles !== b.kept &&
                      <span className="ml-1 text-xs text-slate-400">（清洗后 {b.kept}）</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="p-5" delay={0.1}>
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-3">
          <h3 className="font-semibold text-slate-800 flex-1">脱敏人才画像</h3>
          <div className="w-full sm:w-56">
            <Select value={src} onChange={setSrc} options={[
              { value: '', label: `全部来源（${corpus.total_profiles}）` },
              ...Object.entries(corpus.by_source).map(([k, v]) => ({
                value: k, label: `${SRC_LABEL[k] || k}（${v}）` })),
            ]} />
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {profiles.map(p => (
            <div key={p.id} className="rounded-xl bg-sky-50/70 border border-slate-200/70 p-3.5
                                       hover:bg-sky-100/60 transition">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm font-semibold text-slate-800">{p.code}</span>
                <div className="flex gap-1.5 shrink-0">
                  <Badge tone="slate">{p.language === 'zh' ? '中文' : 'EN'}</Badge>
                  {p.holdout && <Badge tone="amber">留出集</Badge>}
                </div>
              </div>
              <div className="mt-1 text-xs text-slate-500 truncate">
                {SRC_LABEL[p.source_type] || p.source_type} · {p.matched_job_name || p.target_cluster || '未映射'}
              </div>
              <div className="mt-2 text-xs text-slate-600">技能 {p.skill_count} 项</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {p.skills.slice(0, 8).map(s => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-white/80 border border-slate-200/70
                                           text-[11px] text-slate-600">{s}</span>
                ))}
              </div>
              {p.source_url && (
                <a href={p.source_url} target="_blank" rel="noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-[11px] text-sky-600 hover:underline">
                  <ExternalLink className="w-3 h-3" />出处
                </a>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ---------------- 学到的技能表述 ---------------- */
function AliasPanel({ corpus }: { corpus: TalentCorpus }) {
  const [items, setItems] = useState<AliasItem[]>([])
  const [status, setStatus] = useState('accepted')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.talentAliases(status, 300).then(d => setItems(d.items))
      .catch(() => setItems([])).finally(() => setLoading(false))
  }, [status])

  return (
    <div className="space-y-4">
      <Card className="p-6">
        <h3 className="font-semibold text-slate-800 mb-2">简历里学到了什么</h3>
        {/* 限宽：这段是正文不是表格，通栏会拉出一行几十字的长行，很难读 */}
        <p className="max-w-3xl text-sm text-slate-600 leading-relaxed">
          简历的技能写法和 JD 不一样（<code className="text-xs bg-slate-100 px-1 rounded">Numpy</code>
          {' / '}
          <code className="text-xs bg-slate-100 px-1 rounded">Map Reduce</code>），对不齐就等于白抽。
          系统从语料里归集这些写法、对齐到图谱<b className="text-slate-800">已有</b>的技能节点，
          <b className="text-slate-800">不新建节点、不改 JD 侧词典</b>。
        </p>

        {/* 四个数字从句子里拎出来单独成行，便于逐个核对 */}
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span className="text-slate-500">采纳
            <b className="ml-1.5 text-base text-accent-deep tabular-nums">{corpus.alias_accepted}</b>
            <span className="ml-0.5 text-xs">条</span>
          </span>
          <span className="text-slate-500">拒绝
            <b className="ml-1.5 text-base text-slate-700 tabular-nums">{corpus.alias_rejected}</b>
            <span className="ml-0.5 text-xs">条</span>
          </span>
          <span className="h-4 w-px bg-slate-200" aria-hidden />
          <span className="text-slate-500">学习集
            <b className="ml-1.5 text-base text-slate-700 tabular-nums">{corpus.total_profiles - corpus.holdout}</b>
            <span className="ml-0.5 text-xs">份</span>
          </span>
          <span className="text-slate-500">留出集
            <b className="ml-1.5 text-base text-amber-600 tabular-nums">{corpus.holdout}</b>
            <span className="ml-0.5 text-xs">份</span>
            <span className="ml-1 text-xs text-slate-400">（不参与学习，只做对照评测）</span>
          </span>
        </div>

        <p className="mt-3 max-w-3xl text-xs text-slate-500 leading-relaxed">
          下表里不少条目只有 1 份简历佐证，是因为护栏按映射类型分档：
          <b className="text-slate-700">机械型</b>（只差大小写/空格，如 <code className="bg-slate-100 px-1 rounded">JSP→Jsp</code>）
          客观可核验，1 份即可；<b className="text-slate-700">判断型</b>（包含关系、语义相似）
          必须 ≥2 份不同简历佐证，孤例只记候选、不启用。
        </p>
        {/* 二级筛选：刻意做得比顶部主页签轻，避免两处实心色块互相抢视觉 */}
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          {[['accepted', '已采纳', corpus.alias_accepted],
            ['rejected', '已拒绝', corpus.alias_rejected]].map(([k, label, n]: any) => (
            <button key={k} onClick={() => setStatus(k)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                status === k
                  ? 'bg-sky-50 text-sky-700 ring-1 ring-sky-200'
                  : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'}`}>
              {label}
              <span className={`ml-1.5 tabular-nums ${status === k ? 'text-sky-500' : 'text-slate-400'}`}>{n}</span>
            </button>
          ))}
        </div>
      </Card>

      {loading ? <Spinner /> : (
        <Card className="p-5" delay={0.05}>
          <div className="mb-3 rounded-xl bg-sky-50/70 border border-slate-200/70 px-3 py-2
                          text-xs text-slate-500 leading-relaxed">
            左 = 简历里出现的写法　→　右 = 图谱中<b className="text-slate-700">已存在</b>的节点名。
            节点名的大小写以图谱为准（如 <code className="bg-white px-1 rounded border border-slate-200">JDK → jdk</code>），
            对齐的是"哪个节点"，不是"哪种写法好看"。
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
            {items.map(a => (
              <div key={a.alias} className="rounded-xl bg-sky-50/70 border border-slate-200/70 px-3.5 py-2.5">
                <div className="text-sm text-slate-800 truncate">
                  <span className="font-medium">{a.alias}</span>
                  {a.canonical && <span className="text-slate-400"> → </span>}
                  {a.canonical && <span className="text-accent-deep">{a.canonical}</span>}
                </div>
                <div className="text-[11px] text-slate-500 mt-0.5 truncate" title={a.reason || ''}>
                  {a.talent_count} 份简历 · {a.reason || '—'}
                </div>
              </div>
            ))}
          </div>
          {items.length === 0 && <EmptyState text="暂无记录" />}
        </Card>
      )}
    </div>
  )
}
