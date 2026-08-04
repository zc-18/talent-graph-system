import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Upload, Loader2, ShieldCheck, ExternalLink } from 'lucide-react'
import { IUsersThree, IDatabase, ITarget, IShieldCheck } from '../components/icons'
import {
  api, errMsg, JobListItem, TalentCorpus, TalentProfile, SupplyDemand,
  TeamItem, TeamGap, AliasItem,
} from '../api'
import { Card, PageHeader, Badge, Spinner, EmptyState, ErrorState } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'

type Tab = 'team' | 'supply' | 'corpus' | 'alias'

const TABS: { key: Tab; label: string }[] = [
  { key: 'team', label: '团队能力盘点' },
  { key: 'supply', label: '供需缺口对照' },
  { key: 'corpus', label: '简历语料台账' },
  { key: 'alias', label: '学到的技能表述' },
]

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
      <PageHeader
        title="人才与团队盘点"
        subtitle={`${corpus.total_profiles} 份脱敏人才画像 · 共抽取 ${corpus.total_skills_extracted} 项技能 · 简历原文与身份信息不入库`}
        icon={<IUsersThree className="w-7 h-7" />}
      />

      <div className="flex flex-wrap items-center gap-1.5">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-xl text-sm font-medium whitespace-nowrap transition ${
              tab === t.key ? 'bg-grad-accent text-white shadow-glow' : 'btn-ghost'}`}>
            {t.label}
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
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()

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

  const onFile = async (f: File) => {
    if (!teamId) return
    setUploading(true)
    try {
      const r = await api.uploadTeamResume(teamId, f, '新成员', '', '')
      toast('success', `已加入团队：${r.code}，提取 ${r.skill_count} 项技能（原文未留存）`)
      const d = await api.teams(); setTeams(d.items)
      if (jobId) api.teamGap(teamId, jobId).then(setGap).catch(() => {})
      onChanged()
    } catch (e: any) { toast('error', errMsg(e, '简历解析失败，请确认为 PDF/Word 格式')) }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = '' }
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <div className="flex-1 min-w-0">
            <label className="text-xs text-slate-500 mb-1 block">团队</label>
            <Select value={String(teamId ?? '')} onChange={v => setTeamId(Number(v))}
              options={teams.map(t => ({ value: String(t.id), label: `${t.name}（${t.size} 人）` }))} />
          </div>
          <div className="flex-1 min-w-0">
            <label className="text-xs text-slate-500 mb-1 block">目标岗位</label>
            <Select value={String(jobId ?? '')} onChange={v => setJobId(Number(v))}
              options={jobs.map(j => ({ value: String(j.id), label: j.name }))} />
          </div>
          <div className="shrink-0 w-full sm:w-auto">
            <input ref={fileRef} type="file" accept=".pdf,.docx,.doc,.txt" className="hidden"
              onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
            <button onClick={() => fileRef.current?.click()} disabled={uploading || !teamId}
              className="btn-primary w-full sm:w-auto whitespace-nowrap">
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              加入成员简历
            </button>
          </div>
        </div>
        <p className="mt-3 text-xs text-slate-500 flex items-start gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-600" />
          上传的简历只在内存中解析，服务端仅留存脱敏后的技能要素，姓名与联系方式不入库。
        </p>
      </Card>

      {loading && <Spinner label="计算团队能力缺口…" />}
      {!loading && !gap && <EmptyState text="暂无盘点结果" hint="请选择团队与目标岗位" />}
      {!loading && gap && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <KPI label="团队人数" value={`${gap.team.size}`} unit="人" />
            <KPI label="必备能力覆盖" value={`${gap.required_covered}/${gap.required_total}`}
              unit={`${(gap.coverage_rate * 100).toFixed(0)}%`} />
            <KPI label="加权覆盖率" value={`${(gap.weighted_coverage * 100).toFixed(1)}`} unit="%" />
            <KPI label="加分能力覆盖" value={`${gap.bonus_covered}/${gap.bonus_total}`} unit="" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <h3 className="font-semibold text-slate-800 mb-3">
                还缺谁 · 团队没人具备的必备能力
                {gap.missing.length > 0 &&
                  <span className="ml-2 text-xs font-normal text-slate-400">共 {gap.missing.length} 项，按权重降序</span>}
              </h3>
              {gap.missing.length === 0
                ? <EmptyState text="该岗位的必备能力已全部覆盖" />
                : <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                    {gap.missing.map(m => (
                      <div key={m.skill} className="flex items-center justify-between gap-2
                                                    px-3 py-2 rounded-lg bg-rose-50/70 border border-rose-100">
                        <span className="text-sm text-slate-800 truncate">{m.skill}</span>
                        <Badge tone="rose">权重 {m.weight.toFixed(2)}</Badge>
                      </div>
                    ))}
                  </div>}
            </Card>

            <Card>
              <h3 className="font-semibold text-slate-800 mb-3">谁能补 · 成员对必备能力的贡献</h3>
              <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                {gap.contributions.map(c => (
                  <div key={c.member_id} className="px-3 py-2 rounded-lg border border-slate-100 bg-white/60">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800 truncate">
                        {c.display_name}
                        <span className="ml-2 text-xs text-slate-400 font-normal">{c.talent_code}</span>
                      </span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Badge tone="indigo">覆盖 {c.covers_required}</Badge>
                        {c.uniquely_covers > 0 && <Badge tone="amber">独有 {c.uniquely_covers}</Badge>}
                      </div>
                    </div>
                    <div className="mt-1 text-xs text-slate-500 truncate">
                      {c.role_label || '—'} · 技能 {c.skill_count} 项
                      {c.unique_skills.length > 0 && <> · 仅他会：{c.unique_skills.slice(0, 3).join('、')}</>}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
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
          itemStyle: { color: '#34D399', borderRadius: [0, 4, 4, 0] } },
      ],
    }
  }, [data])

  return (
    <div className="space-y-4">
      <Card>
        <label className="text-xs text-slate-500 mb-1 block">目标岗位</label>
        <Select value={String(jobId ?? '')} onChange={v => setJobId(Number(v))}
          options={jobs.map(j => ({ value: String(j.id), label: j.name }))} />
      </Card>
      {loading && <Spinner label="计算供需缺口…" />}
      {!loading && data && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <KPI label="语料人数" value={`${data.corpus_size}`} unit="人" />
            <KPI label="对口人才" value={`${data.aligned_talents}`} unit="人" />
            <KPI label="必备能力被覆盖" value={`${data.required_covered}/${data.required_total}`} unit="" />
            <KPI label="覆盖率" value={`${(data.coverage_rate * 100).toFixed(1)}`} unit="%" />
          </div>
          <Card>
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
      <Card>
        <div className="flex items-start gap-2 text-sm text-emerald-900 bg-emerald-50/70
                        border border-emerald-100 rounded-xl px-3 py-2">
          <IShieldCheck className="w-5 h-5 shrink-0 text-emerald-600" />
          <span>{corpus.privacy_notice}</span>
        </div>
      </Card>

      <Card>
        <h3 className="font-semibold text-slate-800 mb-3">采集批次与许可证</h3>
        <div className="overflow-x-auto -mx-2 px-2">
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

      <Card>
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
            <div key={p.id} className="rounded-xl border border-slate-100 bg-white/60 p-3">
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
                  <span key={s} className="px-1.5 py-0.5 rounded bg-slate-100 text-[11px] text-slate-600">{s}</span>
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
      <Card>
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
            <b className="ml-1.5 text-base text-emerald-700 tabular-nums">{corpus.alias_accepted}</b>
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
        <Card>
          <div className="mb-3 rounded-lg bg-slate-50 border border-slate-100 px-3 py-2
                          text-xs text-slate-500 leading-relaxed">
            左 = 简历里出现的写法　→　右 = 图谱中<b className="text-slate-700">已存在</b>的节点名。
            节点名的大小写以图谱为准（如 <code className="bg-white px-1 rounded border border-slate-200">JDK → jdk</code>），
            对齐的是"哪个节点"，不是"哪种写法好看"。
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
            {items.map(a => (
              <div key={a.alias} className="px-3 py-2 rounded-lg border border-slate-100 bg-white/60">
                <div className="text-sm text-slate-800 truncate">
                  <span className="font-medium">{a.alias}</span>
                  {a.canonical && <span className="text-slate-400"> → </span>}
                  {a.canonical && <span className="text-emerald-700">{a.canonical}</span>}
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

function KPI({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <Card>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-2xl font-semibold text-slate-800 tabular-nums">{value}</span>
        {unit && <span className="text-sm text-slate-500">{unit}</span>}
      </div>
    </Card>
  )
}
