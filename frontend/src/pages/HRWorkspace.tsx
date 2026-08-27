import { useEffect, useMemo, useRef, useState } from 'react'
import { BriefcaseBusiness, Check, FileArchive, Loader2, RefreshCw, Upload, UsersRound } from 'lucide-react'
import { api, errMsg, JobListItem, RankingItem, RecruitmentBatch, TeamItem } from '../api'
import { Badge, Card, EmptyState, ErrorState, Meter, Spinner } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'

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
  const [error, setError] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()

  const load = async () => {
    setError(false)
    try {
      const [jobData, batchData, teamData] = await Promise.all([api.jobs({ page: 1, size: 100 }), api.recruitmentBatches({ page: 1, size: 100 }), api.teams()])
      setJobs(jobData.items || []); setBatches(batchData.items || []); setTeams(teamData.items || [])
      if (!jobId && jobData.items?.[0]) setJobId(jobData.items[0].id)
      if (!batchId && batchData.items?.[0]) setBatchId(batchData.items[0].id)
    } catch { setError(true) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])

  const current = batches.find(item => item.id === batchId)
  const loadRanking = async (id: number) => {
    try { const data = await api.recruitmentRanking(id, { page: 1, size: 200 }); setRanking(data.items || []) }
    catch { setRanking([]) }
  }
  useEffect(() => { if (batchId) { setSelected(new Set()); void loadRanking(batchId) } }, [batchId])

  useEffect(() => {
    if (!current || !['queued', 'parsing', 'matching', 'processing'].includes(current.status)) return
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.recruitmentBatch(current.id)
        setBatches(list => list.map(item => item.id === latest.id ? latest : item))
        if (['completed', 'completed_with_errors'].includes(latest.status)) void loadRanking(latest.id)
      } catch { /* the next manual refresh remains available */ }
    }, 3000)
    return () => window.clearInterval(timer)
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
  if (error) return <ErrorState text="HR 工作台加载失败" onRetry={load} />
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3"><div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow"><BriefcaseBusiness className="w-5 h-5 text-white" /></div><div><h1 className="text-2xl font-extrabold text-slate-900">HR 招聘工作台</h1><p className="text-sm text-slate-500">批量解析、统一岗位契约排名、Top-K 入团队</p></div></div>
        <button onClick={() => void load()} className="btn-ghost"><RefreshCw className="w-4 h-4" /> 刷新</button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[360px_minmax(0,1fr)] gap-5 items-start">
        <div className="space-y-4">
          <Card className="p-5 space-y-3">
            <div className="label">创建招聘批次</div>
            <input value={name} onChange={e => setName(e.target.value)} className="input" placeholder="例：测试开发 8 月批次" />
            <Select value={jobId ?? ''} onChange={value => setJobId(Number(value))} label="目标岗位" options={jobs.map(job => ({ value: String(job.id), label: `${job.name} · v${job.version}` }))} />
            <label className="block"><span className="text-xs text-slate-500">保留天数</span><input type="number" min={1} max={180} value={retentionDays} onChange={e => setRetentionDays(Number(e.target.value))} className="input mt-1" /></label>
            <button onClick={createBatch} disabled={busy} className="btn-primary w-full justify-center">{busy && <Loader2 className="w-4 h-4 animate-spin" />} 创建批次</button>
          </Card>

          <Card className="p-5 space-y-3">
            <div className="label">当前批次</div>
            <Select value={batchId ?? ''} onChange={value => setBatchId(Number(value))} label="选择批次" options={batches.map(batch => ({ value: String(batch.id), label: batch.name || `批次 #${batch.id}` }))} />
            {current ? <><div className="flex items-center justify-between text-xs"><Badge tone={current.status === 'completed' ? 'emerald' : current.status === 'completed_with_errors' ? 'amber' : current.status === 'failed' ? 'rose' : 'cyan'}>{current.status}</Badge><span className="text-slate-500 tabular-nums">{progress.processed}/{progress.total}</span></div><Meter value={progressRate} /><div className="grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded-lg bg-emerald-50 p-2"><b className="block text-emerald-700">{progress.succeeded}</b>成功</div><div className="rounded-lg bg-rose-50 p-2"><b className="block text-rose-700">{progress.failed}</b>失败</div><div className="rounded-lg bg-sky-50 p-2"><b className="block text-sky-700">{progress.total - progress.processed}</b>待处理</div></div></> : <EmptyState text="暂无批次" />}
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
          {ranking.length === 0 ? <div className="p-6"><EmptyState text="暂无候选人排名" hint="上传简历并等待解析、匹配完成" /></div> : (
            <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-xs text-slate-500 bg-slate-50"><tr><th className="p-3 text-left w-12">选择</th><th className="p-3 text-left">排名/候选人</th><th className="p-3 text-left">综合分</th><th className="p-3 text-left">维度</th><th className="p-3 text-left">状态</th></tr></thead><tbody>{ranking.map(row => <tr key={row.candidate_id} className="border-t border-slate-100 hover:bg-sky-50/40"><td className="p-3"><button aria-label={`选择 ${row.code}`} onClick={() => setSelected(prev => { const next = new Set(prev); next.has(row.candidate_id) ? next.delete(row.candidate_id) : next.add(row.candidate_id); return next })} className={`w-5 h-5 rounded border grid place-items-center ${selected.has(row.candidate_id) ? 'bg-accent text-white border-accent' : 'border-slate-300'}`}>{selected.has(row.candidate_id) && <Check className="w-3.5 h-3.5" />}</button></td><td className="p-3"><b className="text-slate-800 mr-2">#{row.rank}</b><span className="text-slate-600">{row.code}</span></td><td className="p-3 font-extrabold text-lg text-slate-900">{score(row.overall_score)}</td><td className="p-3"><div className="flex flex-wrap gap-1">{Object.entries(row.dimension_scores || {}).slice(0, 4).map(([key, value]) => <Badge key={key} tone="slate">{key} {score(value)}</Badge>)}</div></td><td className="p-3"><Badge tone="cyan">{row.status}</Badge></td></tr>)}</tbody></table></div>
          )}
          <div className="p-5 border-t border-slate-200 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3"><div className="text-sm text-slate-500">已选 <b className="text-slate-900">{selectedRows.length}</b> 人</div><div className="flex flex-col sm:flex-row gap-2"><Select value={teamId ?? ''} onChange={value => setTeamId(value ? Number(value) : null)} placeholder="新建 Top-K 团队" className="sm:w-52" options={[{ value: '', label: '新建 Top-K 团队' }, ...teams.map(team => ({ value: String(team.id), label: team.name }))]} /><button onClick={commitSelection} disabled={busy || selected.size === 0} className="btn-primary justify-center"><UsersRound className="w-4 h-4" /> {teamId ? '加入团队' : '创建团队并入组'}</button></div></div>
          {current?.failures && current.failures.length > 0 && <div className="p-5 border-t border-rose-100 bg-rose-50/60"><div className="flex items-center gap-2 text-sm font-semibold text-rose-700"><FileArchive className="w-4 h-4" /> 失败明细</div><div className="text-xs text-rose-600 mt-2 space-y-1">{current.failures.slice(0, 10).map((failure: any, index) => <div key={index}>{failure.filename || failure.code || `文件 ${index + 1}`} · {failure.detail || failure.reason}</div>)}</div></div>}
        </Card>
      </div>
    </div>
  )
}
