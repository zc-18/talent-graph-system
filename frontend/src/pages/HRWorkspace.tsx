import { useEffect, useMemo, useRef, useState } from 'react'
import { BriefcaseBusiness, Check, FileArchive, Loader2, RefreshCw, Upload, UsersRound } from 'lucide-react'
import { api, errMsg, JobListItem, RankingItem, RecruitmentBatch, TeamItem } from '../api'
import { Badge, Card, EmptyState, ErrorState, Meter, Spinner } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'

const ACTIVE_STATUSES = ['queued', 'parsing', 'matching', 'processing']
const TERMINAL_STATUSES = ['completed', 'completed_with_errors', 'partial_failed']
// 批次轮询同样要有出口：连续 5 次拿不到状态就停下并把真实原因显示出来，
// 每次失败把间隔翻倍到 30 秒封顶，别在后端已经挂了的情况下继续每 3 秒敲一次。
const POLL_START_MS = 3000
const POLL_MAX_MS = 30000
const POLL_MAX_FAILURES = 5

interface SectionErrors { jobs?: string; batches?: string; teams?: string }

function SectionError({ message }: { message: string }) {
  return (
    <div className="rounded-lg bg-rose-50 border border-rose-100 px-3 py-2 text-xs text-rose-700">
      {message}
    </div>
  )
}

export default function HRWorkspace() {
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [teams, setTeams] = useState<TeamItem[]>([])
  const [batches, setBatches] = useState<RecruitmentBatch[]>([])
  const [batchId, setBatchId] = useState<number | null>(null)
  const [ranking, setRanking] = useState<RankingItem[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [jobId, setJobId] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [retentionDays, setRetentionDays] = useState(30)
  const [authorized, setAuthorized] = useState(false)
  const [topK, setTopK] = useState(5)
  const [teamId, setTeamId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<SectionErrors>({})
  const [rankingError, setRankingError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()

  // 分区降级而不是 Promise.all 全或无：admin 打开本页时 /hr/recruitment-batches 会 403
  // （require_hr 不含 admin），而岗位与团队两个接口是 200 的。以前一个 reject 就把整页
  // 变成"HR 工作台加载失败"，既盖掉了能用的部分，也把真实原因（角色无权）吞了。
  const load = async () => {
    setLoading(true)
    const [jobResult, batchResult, teamResult] = await Promise.allSettled([
      api.jobs({ page: 1, size: 100 }),
      api.recruitmentBatches({ page: 1, size: 100 }),
      api.teams(),
    ])
    const next: SectionErrors = {}
    if (jobResult.status === 'fulfilled') {
      setJobs(jobResult.value.items || [])
      if (!jobId && jobResult.value.items?.[0]) setJobId(jobResult.value.items[0].id)
    } else next.jobs = errMsg(jobResult.reason, '岗位列表加载失败')
    if (batchResult.status === 'fulfilled') {
      setBatches(batchResult.value.items || [])
      if (!batchId && batchResult.value.items?.[0]) setBatchId(batchResult.value.items[0].id)
    } else next.batches = errMsg(batchResult.reason, '招聘批次加载失败')
    if (teamResult.status === 'fulfilled') setTeams(teamResult.value.items || [])
    else next.teams = errMsg(teamResult.reason, '团队列表加载失败')
    setErrors(next)
    setLoading(false)
  }
  useEffect(() => { void load() }, [])

  const current = batches.find(item => item.id === batchId)
  // 失败不再静默清空：空数组会被渲染成"暂无候选人排名"，把一次请求失败伪装成
  // 一个业务事实，排查时完全看不出这里发生过错误。
  const loadRanking = async (id: number) => {
    setRankingError(null)
    try { const data = await api.recruitmentRanking(id, { page: 1, size: 200 }); setRanking(data.items || []) }
    catch (error) { setRanking([]); setRankingError(errMsg(error, '候选人排名加载失败')) }
  }
  useEffect(() => { if (batchId) { setSelected(new Set()); void loadRanking(batchId) } }, [batchId])

  useEffect(() => {
    if (!current || !ACTIVE_STATUSES.includes(current.status)) return
    let cancelled = false
    let failures = 0
    let delay = POLL_START_MS
    let timer = 0
    const tick = async () => {
      try {
        const latest = await api.recruitmentBatch(current.id)
        if (cancelled) return
        failures = 0; delay = POLL_START_MS
        setBatches(list => list.map(item => item.id === latest.id ? latest : item))
        if (TERMINAL_STATUSES.includes(latest.status)) { void loadRanking(latest.id); return }
        if (latest.status === 'failed') return
      } catch (error) {
        if (cancelled) return
        failures += 1
        if (failures >= POLL_MAX_FAILURES) {
          setErrors(prev => ({ ...prev, batches: errMsg(error, '批次状态轮询失败，请手动刷新') }))
          return
        }
        delay = Math.min(delay * 2, POLL_MAX_MS)
      }
      if (!cancelled) timer = window.setTimeout(tick, delay)
    }
    timer = window.setTimeout(tick, delay)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [current?.id, current?.status])

  const createBatch = async () => {
    if (!jobId || !name.trim()) { toast('error', '请填写批次名称并选择岗位'); return }
    setBusy(true)
    try {
      const created = await api.createRecruitmentBatch({ name: name.trim(), target_job_id: jobId })
      setBatches(list => [created, ...list]); setBatchId(created.id); setName(''); toast('success', '招聘批次已创建')
    } catch (error) { toast('error', errMsg(error, '批次创建失败')) }
    finally { setBusy(false) }
  }

  const upload = async (files: File[]) => {
    if (!batchId || files.length === 0) return
    if (!authorized) { toast('error', '请先确认已获得候选人数据处理授权'); return }
    setBusy(true)
    try {
      const updated = await api.uploadRecruitmentFiles(batchId, files, true, retentionDays)
      setBatches(list => list.map(item => item.id === updated.id ? updated : item)); toast('success', `已提交 ${files.length} 个文件处理`)
    } catch (error) { toast('error', errMsg(error, '批量文件上传失败')) }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }

  const applyTopK = () => setSelected(new Set(ranking.slice(0, Math.max(1, topK)).map(item => item.candidate_id)))
  const commitSelection = async () => {
    if (!batchId || selected.size === 0) { toast('error', '请至少选择一名候选人'); return }
    setBusy(true)
    try {
      const result = await api.selectRecruitmentCandidates(batchId, { candidate_ids: [...selected], team_id: teamId })
      const before = Math.round(Number(result.before_coverage || 0) * 100)
      const after = Math.round(Number(result.after_coverage || 0) * 100)
      toast('success', `${teamId ? '目标团队已入组' : '新团队已创建并入组'} ${selected.size} 人，覆盖率 ${before}% → ${after}%`)
      await loadRanking(batchId)
    } catch (error) { toast('error', errMsg(error, '候选人入组失败')) }
    finally { setBusy(false) }
  }

  const progress = current?.progress || { total: 0, processed: 0, succeeded: 0, failed: 0 }
  const progressRate = progress.total ? progress.processed / progress.total : 0
  const score = (value: number) => value <= 1 ? Math.round(value * 100) : Math.round(value)
  const selectedRows = useMemo(() => ranking.filter(item => selected.has(item.candidate_id)), [ranking, selected])

  if (loading) return <Spinner />
  // 只有三块全挂才整页报错；任何一块还活着就渲染出来并在对应分区上挂真实原因。
  if (errors.jobs && errors.batches && errors.teams)
    return <ErrorState text={errors.batches} onRetry={load} />
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3"><div className="w-11 h-11 rounded-xl bg-grad-accent ring-1 ring-accent/20 grid place-items-center shadow-glow"><BriefcaseBusiness className="w-5 h-5 text-accent-deep" /></div><div><h1 className="text-2xl font-extrabold text-slate-900">HR 招聘工作台</h1><p className="text-sm text-slate-500">批量解析、统一岗位契约排名、Top-K 入团队</p></div></div>
        <button onClick={() => void load()} className="btn-ghost"><RefreshCw className="w-4 h-4" /> 刷新</button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[360px_minmax(0,1fr)] gap-5 items-start">
        <div className="space-y-4">
          <Card className="p-5 space-y-3">
            <div className="label">创建招聘批次</div>
            {errors.jobs && <SectionError message={errors.jobs} />}
            <input value={name} onChange={e => setName(e.target.value)} className="input" placeholder="例：测试开发 8 月批次" />
            <Select value={jobId ?? ''} onChange={value => setJobId(Number(value))} label="目标岗位" options={jobs.map(job => ({ value: String(job.id), label: `${job.name} · v${job.version}` }))} />
            <label className="block"><span className="text-xs text-slate-500">保留天数</span><input type="number" min={1} max={180} value={retentionDays} onChange={e => setRetentionDays(Number(e.target.value))} className="input mt-1" /></label>
            <button onClick={createBatch} disabled={busy} className="btn-primary w-full justify-center">{busy && <Loader2 className="w-4 h-4 animate-spin" />} 创建批次</button>
          </Card>

          <Card className="p-5 space-y-3">
            <div className="label">当前批次</div>
            {errors.batches && <SectionError message={errors.batches} />}
            <Select value={batchId ?? ''} onChange={value => setBatchId(Number(value))} label="选择批次" options={batches.map(batch => ({ value: String(batch.id), label: batch.name || `批次 #${batch.id}` }))} />
            {current ? <><div className="flex items-center justify-between text-xs"><Badge tone={current.status === 'completed' ? 'emerald' : current.status === 'completed_with_errors' ? 'amber' : current.status === 'failed' ? 'rose' : 'cyan'}>{current.status}</Badge><span className="text-slate-500 tabular-nums">{progress.processed}/{progress.total}</span></div><Meter value={progressRate} /><div className="grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded-lg bg-accent/8 p-2"><b className="block text-accent-deep">{progress.succeeded}</b>成功</div><div className="rounded-lg bg-rose-50 p-2"><b className="block text-rose-700">{progress.failed}</b>失败</div><div className="rounded-lg bg-sky-50 p-2"><b className="block text-sky-700">{progress.total - progress.processed}</b>待处理</div></div></> : <EmptyState text="暂无批次" />}
            <label className="flex items-start gap-2 text-xs text-slate-500 leading-relaxed"><input type="checkbox" checked={authorized} onChange={e => setAuthorized(e.target.checked)} className="mt-0.5 accent-accent" /><span>我确认已获得候选人简历的处理授权，结构化画像将按保留期自动清理。</span></label>
            <button onClick={() => fileRef.current?.click()} disabled={!batchId || busy} className="btn-ghost w-full justify-center"><Upload className="w-4 h-4" /> 上传 PDF / DOCX / ZIP</button>
            <input ref={fileRef} type="file" multiple accept=".pdf,.docx,.zip" className="hidden" onChange={e => void upload(Array.from(e.target.files || []))} />
          </Card>
        </div>

        <Card className="p-0 overflow-hidden">
          <div className="p-5 border-b border-slate-200 flex items-center justify-between gap-3 flex-wrap">
            <div><div className="font-bold text-slate-800">候选人排名</div><div className="text-xs text-slate-400 mt-0.5">历史排名固定引用批次创建时的岗位版本</div></div>
            <div className="flex items-center gap-2"><input aria-label="Top K 数量" type="number" min={1} max={ranking.length || 30} value={topK} onChange={e => setTopK(Number(e.target.value))} className="input !w-20" /><button onClick={applyTopK} className="btn-ghost">Top-K</button></div>
          </div>
          {rankingError ? <div className="p-6"><ErrorState text={rankingError} onRetry={() => batchId && void loadRanking(batchId)} /></div>
            : ranking.length === 0 ? <div className="p-6"><EmptyState text="暂无候选人排名" hint="上传简历并等待解析、匹配完成" /></div> : (
            <div className="overflow-x-auto"><table className="w-full text-sm min-w-[640px]"><thead className="text-xs text-slate-500 bg-slate-50"><tr><th className="p-3 text-left w-12">选择</th><th className="p-3 text-left">排名/候选人</th><th className="p-3 text-left">综合分</th><th className="p-3 text-left">维度</th><th className="p-3 text-left">状态</th></tr></thead><tbody>{ranking.map(row => <tr key={row.candidate_id} className="border-t border-slate-100 hover:bg-sky-50/40"><td className="p-3"><button aria-label={`选择 ${row.code}`} onClick={() => setSelected(prev => { const next = new Set(prev); next.has(row.candidate_id) ? next.delete(row.candidate_id) : next.add(row.candidate_id); return next })} className={`w-5 h-5 rounded border grid place-items-center ${selected.has(row.candidate_id) ? 'bg-accent text-white border-accent' : 'border-slate-300'}`}>{selected.has(row.candidate_id) && <Check className="w-3.5 h-3.5" />}</button></td><td className="p-3"><b className="text-slate-800 mr-2">#{row.rank}</b><span className="text-slate-600">{row.code}</span></td><td className="p-3 font-extrabold text-lg text-slate-900">{score(row.overall_score)}</td><td className="p-3"><div className="flex flex-wrap gap-1">{Object.entries(row.dimension_scores || {}).slice(0, 4).map(([key, value]) => <Badge key={key} tone="slate">{key} {score(value)}</Badge>)}</div></td><td className="p-3"><Badge tone="cyan">{row.status}</Badge></td></tr>)}</tbody></table></div>
          )}
          <div className="p-5 border-t border-slate-200 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3"><div className="text-sm text-slate-500">{errors.teams ? <span className="text-rose-600">{errors.teams}</span> : <>已选 <b className="text-slate-900">{selectedRows.length}</b> 人</>}</div><div className="flex flex-col sm:flex-row gap-2"><Select value={teamId ?? ''} onChange={value => setTeamId(value ? Number(value) : null)} placeholder="新建 Top-K 团队" className="sm:w-52" options={[{ value: '', label: '新建 Top-K 团队' }, ...teams.map(team => ({ value: String(team.id), label: team.name }))]} /><button onClick={commitSelection} disabled={busy || selected.size === 0} className="btn-primary justify-center"><UsersRound className="w-4 h-4" /> {teamId ? '加入团队' : '创建团队并入组'}</button></div></div>
          {current?.failures && current.failures.length > 0 && <div className="p-5 border-t border-rose-100 bg-rose-50/60"><div className="flex items-center gap-2 text-sm font-semibold text-rose-700"><FileArchive className="w-4 h-4" /> 失败明细</div><div className="text-xs text-rose-600 mt-2 space-y-1">{current.failures.slice(0, 10).map((failure: any, index) => <div key={index}>{failure.filename || failure.code || `文件 ${index + 1}`} · {failure.detail || failure.reason}</div>)}</div></div>}
        </Card>
      </div>
    </div>
  )
}
