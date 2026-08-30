import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ExternalLink, GitBranch, Loader2, Pencil, Save, Search, Send, X } from 'lucide-react'
import { ISparkle, IGlobe, ILightning } from '../components/icons'
import { api, errMsg, DiscoveryRunResult } from '../api'
import { Badge, Card, ConfidencePill, ErrorState, Meter } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'

const TRACKS = [
  { value: '', label: '自动判定轨道' }, { value: 'software', label: '软件' }, { value: 'hardware', label: '硬件' },
  { value: 'algorithm', label: '算法' }, { value: 'data', label: '数据' }, { value: 'ops', label: '运维' }, { value: 'product', label: '产品' },
]
const SENIORITIES = [{ value: '', label: '不限级别' }, { value: 'junior', label: '初级' }, { value: 'middle', label: '中级' }, { value: 'senior', label: '高级' }]
const RECRUITMENT = [{ value: '', label: '校招/社招' }, { value: 'campus', label: '校招' }, { value: 'social', label: '社招' }]

// 一次发现要跑联网检索 + 大模型，正常十几秒、最坏三分钟。轮询从 3 秒起按 1.5 倍退避
// 到 15 秒封顶：早期给快速反馈，长任务不至于把请求打成一片。次数与连续失败都设上限，
// 否则一个挂掉的后端会让这页每 3 秒发一次请求直到用户关标签页。
const POLL_START_MS = 3000
const POLL_MAX_MS = 15000
const POLL_MAX_ATTEMPTS = 40
const POLL_MAX_FAILURES = 3
const RUN_STORAGE_KEY = 'talent-graph:discovery-run'

export default function Discovery() {
  const toast = useToast()
  const [seeds, setSeeds] = useState<string[]>([])
  const [keyword, setKeyword] = useState('')
  const [track, setTrack] = useState('')
  const [seniority, setSeniority] = useState('')
  const [recruitmentType, setRecruitmentType] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [definition, setDefinition] = useState<any>(null)
  const [editing, setEditing] = useState(false)
  const [changeNote, setChangeNote] = useState('')
  const [failure, setFailure] = useState<string | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const timer = useRef<number | null>(null)
  const attempts = useRef(0)
  const failures = useRef(0)

  useEffect(() => { api.seeds().then(data => setSeeds(data.seeds || [])).catch(() => setSeeds([])) }, [])

  const stopPolling = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
  }
  useEffect(() => stopPolling, [])

  // run_id 同时写进 URL 与 sessionStorage：URL 让这次任务可以直接分享/刷新恢复，
  // sessionStorage 兜住"切到别的页再回来"——那种情况下 URL 上的 query 已经没了。
  const rememberRun = (id: number | null) => {
    try {
      if (id) sessionStorage.setItem(RUN_STORAGE_KEY, String(id))
      else sessionStorage.removeItem(RUN_STORAGE_KEY)
    } catch { /* 隐私模式下 sessionStorage 直接抛，URL 仍然管用 */ }
    const next = new URLSearchParams(searchParams)
    if (id) next.set('run', String(id)); else next.delete('run')
    setSearchParams(next, { replace: true })
  }

  const settle = (data: DiscoveryRunResult) => {
    stopPolling(); setLoading(false); rememberRun(null)
    if (data.status === 'failed') {
      const reason = data.error || '发现任务执行失败'
      setFailure(reason); toast('error', reason); return
    }
    setResult(data)
    setDefinition(data.candidate?.definition || (data as any).definition || null)
  }

  const pollRun = (id: number) => {
    stopPolling()
    const tick = async () => {
      try {
        const data = await api.discoveryRunResult(id)
        failures.current = 0
        if (data.status === 'completed' || data.status === 'failed') { settle(data); return }
      } catch (error) {
        // 404 说明这条任务不属于当前会话（换账号或登录过期），重试多少次都没用。
        if ((error as any)?.response?.status === 404) {
          stopPolling(); setLoading(false); rememberRun(null)
          setFailure('该发现任务不存在或已不属于当前账号'); return
        }
        failures.current += 1
        if (failures.current >= POLL_MAX_FAILURES) {
          stopPolling(); setLoading(false)
          setFailure(errMsg(error, '发现任务状态查询失败，请稍后重试')); return
        }
      }
      attempts.current += 1
      if (attempts.current >= POLL_MAX_ATTEMPTS) {
        stopPolling(); setLoading(false)
        setFailure('发现任务仍在执行，已停止自动刷新——稍后重新打开本页可继续查看结果')
        return
      }
      const delay = Math.min(POLL_START_MS * Math.pow(1.5, attempts.current), POLL_MAX_MS)
      timer.current = window.setTimeout(tick, delay)
    }
    timer.current = window.setTimeout(tick, POLL_START_MS)
  }

  const run = async (value = keyword, selectedTrack = track) => {
    if (!value.trim()) return
    stopPolling(); attempts.current = 0; failures.current = 0
    setLoading(true); setResult(null); setDefinition(null); setEditing(false); setFailure(null)
    try {
      const response = await api.discoveryRun({
        keyword: value.trim(), track: selectedTrack || undefined,
        seniority: seniority || undefined, recruitment_type: recruitmentType || undefined,
      }, { async: true })
      // 后端若同步跑完（老版本或 async_mode 被忽略）也照样能渲染，不用分两套逻辑。
      if (response.status === 'completed' || response.status === 'failed') { settle(response); return }
      rememberRun(response.run_id)
      pollRun(response.run_id)
    } catch (error) {
      setLoading(false)
      toast('error', errMsg(error, '发现任务失败，请检查网络检索与模型服务'))
    }
  }

  // 挂载时恢复未完成的任务：切页回来、刷新、甚至直接贴 URL 都能接着看结果。
  useEffect(() => {
    let stored: string | null = searchParams.get('run')
    if (!stored) { try { stored = sessionStorage.getItem(RUN_STORAGE_KEY) } catch { stored = null } }
    const id = Number(stored)
    if (!stored || !Number.isFinite(id) || id <= 0) return
    attempts.current = 0; failures.current = 0
    setLoading(true)
    api.discoveryRunResult(id).then(data => {
      if (data.status === 'completed' || data.status === 'failed') { settle(data); return }
      rememberRun(id)
      pollRun(id)
    }).catch(() => { setLoading(false); rememberRun(null) })
  }, [])

  const classification = result?.classification || (result?.definition ? 'NEW' : result?.verdict)
  const runData = result?.run || { evidence: result?.candidate?.evidence || [], signals: result?.candidate?.signals || {} }
  const matchedJob = result?.matched_job || (result?.conflict?.job_id ? { id: result.conflict.job_id, name: result.conflict.name || keyword } : null)
  const candidate = result?.candidate
  const candidates = runData?.conditions?.candidates || runData?.signals?.candidates || result?.candidates || []

  const saveDraft = async () => {
    if (!candidate?.id || !definition?.job_title) return
    setBusy(true)
    try {
      const updated = await api.updateDiscoveryCandidate(candidate.id, { definition, change_note: changeNote.trim() || '人工优化' })
      setResult((current: any) => ({ ...current, candidate: updated })); setDefinition(updated.definition); setEditing(false); setChangeNote('')
      toast('success', `候选草稿已保存为 r${updated.current_revision_number || updated.current_revision?.revision}`)
    } catch (error) { toast('error', errMsg(error, '候选草稿保存失败')) }
    finally { setBusy(false) }
  }

  const submit = async () => {
    if (!candidate?.id) return
    setBusy(true)
    try {
      const updated = await api.submitDiscoveryCandidate(candidate.id)
      setResult((current: any) => ({ ...current, candidate: updated })); toast('success', '候选已提交管理员审核')
    } catch (error) { toast('error', errMsg(error, '提交审核失败')) }
    finally { setBusy(false) }
  }

  const capabilities = definition?.capabilities || [
    ...(definition?.required_skills || []).map((item: any) => ({ ...item, importance: 'required' })),
    ...(definition?.bonus_skills || []).map((item: any) => ({ ...item, importance: 'bonus' })),
  ]
  const signals = useMemo(() => {
    const raw = runData?.signals || {}
    const numberOrNull = (...keys: string[]) => {
      const value = keys.map(key => raw[key]).find(item => typeof item === 'number')
      return typeof value === 'number' && Number.isFinite(value) ? value : null
    }
    return [
      { key: 'authority', label: '权威度', value: numberOrNull('authority_strength', 'authority') },
      { key: 'history', label: '历史新颖性', value: raw.history_available === false ? null : numberOrNull('historical_novelty'), empty: '历史样本不足' },
      { key: 'employer', label: '雇主扩散', value: numberOrNull('employer_diffusion') },
      { key: 'market', label: '市场覆盖', value: numberOrNull('market_coverage', 'market_spread') },
      { key: 'naming', label: '命名新颖性', value: numberOrNull('naming_novelty') },
    ]
  }, [runData])

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3"><div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow"><ISparkle className="w-6 h-6 text-white" /></div><div><h1 className="text-2xl font-extrabold text-body-1">新岗位发现与定义</h1><p className="text-sm text-body-2">成熟职业否决 · 结构化消歧 · 多雇主证据 · 候选审核发布</p></div></div>

      <Card className="p-5 space-y-3">
        <div className="flex flex-col sm:flex-row gap-2"><div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-white/80 border border-line-soft/8 flex-1"><Search className="w-4 h-4 text-body-3" /><input value={keyword} onChange={event => setKeyword(event.target.value)} onKeyDown={event => event.key === 'Enter' && void run()} className="bg-transparent outline-none text-sm flex-1 min-w-0" placeholder="输入岗位名称，如：软件系统测试工程师" /></div><button onClick={() => void run()} disabled={loading} className="btn-primary justify-center">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ILightning className="w-4 h-4" />} 检索并判定</button></div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2"><Select value={track} onChange={setTrack} options={TRACKS} label="岗位轨道" /><Select value={seniority} onChange={setSeniority} options={SENIORITIES} label="岗位级别" /><Select value={recruitmentType} onChange={setRecruitmentType} options={RECRUITMENT} label="招聘类型" /></div>
        <div className="flex flex-wrap gap-1.5">{seeds.slice(0, 12).map(seed => <button key={seed} onClick={() => { setKeyword(seed); void run(seed) }} className="chip border bg-white/70 border-line-soft/8 text-body-2 hover:border-accent/40">{seed}</button>)}</div>
      </Card>

      {loading && <Card className="p-10 text-center"><Loader2 className="w-8 h-8 animate-spin mx-auto text-accent" /><p className="text-sm text-body-2 mt-3">正在进行成熟岗位否决、历史新颖性与多雇主证据核验…</p><p className="text-xs text-body-3 mt-1.5">任务在后台执行，切到其他页面再回来仍可看到结果</p></Card>}
      {!loading && failure && <Card className="p-6"><ErrorState text={failure} onRetry={() => void run()} /></Card>}
      {!loading && !failure && !result && <Card className="p-7"><div className="flex flex-col sm:flex-row items-center gap-6"><img src="/empty-discovery.webp" alt="" className="w-40 h-40 object-contain shrink-0 mix-blend-multiply drop-shadow-[0_12px_24px_rgba(14,165,233,0.18)]" /><div className="grid sm:grid-cols-3 gap-4 flex-1">{[[IGlobe, '不同雇主实体', '平台仅是传播渠道，同公司跨平台不重复计数'], [AlertTriangle, '成熟职业否决', 'Java、前端、测试等成熟岗位转入演化'], [Pencil, '草稿可修订', '修订与审核记录只追加、不覆盖']].map(([Icon, title, text]: any) => <div key={title} className="border-t-2 border-accent/25 pt-4"><Icon className="w-5 h-5 text-accent" /><div className="font-bold text-body-1 mt-3">{title}</div><p className="text-xs text-body-2 mt-1 leading-relaxed">{text}</p></div>)}</div></div></Card>}

      {!loading && classification === 'ESTABLISHED' && <Card className="p-6 border-accent/30"><div className="flex items-start gap-3"><CheckCircle2 className="w-6 h-6 text-accent-deep shrink-0" /><div className="flex-1"><Badge tone="emerald">既有岗位</Badge><h2 className="text-xl font-extrabold text-body-1 mt-2">{matchedJob?.name || keyword}</h2><p className="text-sm text-body-2 mt-2">命中成熟职业词典或正式岗位别名，不会创建重复候选。</p><div className="flex flex-wrap gap-2 mt-4">{matchedJob?.id && <Link to={`/jobs/${matchedJob.id}`} className="btn-primary">查看岗位画像</Link>}{matchedJob?.id && <Link to="/evolution" state={{ jobId: matchedJob.id }} className="btn-ghost"><GitBranch className="w-4 h-4" /> 查看演化</Link>}</div></div></div></Card>}

      {!loading && classification === 'AMBIGUOUS' && <Card className="p-6 border-warn/25"><div className="flex items-start gap-3"><AlertTriangle className="w-6 h-6 text-warn shrink-0" /><div className="flex-1"><Badge tone="amber">需要消歧</Badge><h2 className="text-xl font-extrabold text-body-1 mt-2">请明确岗位轨道</h2><p className="text-sm text-body-2 mt-2">当前输入可能混合软件、硬件或行业测试语料，选择后重新判定。</p><div className="flex flex-wrap gap-2 mt-4">{candidates.map((item: any, index: number) => { const selectedTrack = typeof item === 'string' ? '' : item.track || item.value || ''; const candidateTitle = typeof item === 'string' ? item : item.canonical_title || keyword; return <button key={`${selectedTrack}-${candidateTitle}-${index}`} onClick={() => { setTrack(selectedTrack); void run(candidateTitle, selectedTrack) }} className="btn-ghost">{typeof item === 'string' ? item : item.label || item.canonical_title || item.track}</button> })}</div></div></div></Card>}

      {!loading && classification === 'NEW' && definition && (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5 items-start">
          <Card className="p-6">
            <div className="flex items-start justify-between gap-3 flex-wrap"><div><div className="flex items-center gap-2"><Badge tone="amber">新岗位候选</Badge>{candidate && <Badge tone="cyan">{candidate.status} · r{candidate.current_revision_number || candidate.current_revision?.revision}</Badge>}</div>{editing ? <input value={definition.job_title || ''} onChange={event => setDefinition({ ...definition, job_title: event.target.value })} className="input mt-3 text-lg font-bold" /> : <h2 className="text-2xl font-extrabold text-body-1 mt-3">{definition.job_title}</h2>}</div>{candidate && ['draft', 'rejected'].includes(candidate.status) && <button onClick={() => setEditing(value => !value)} className="btn-ghost">{editing ? <X className="w-4 h-4" /> : <Pencil className="w-4 h-4" />} {editing ? '取消' : '优化草稿'}</button>}</div>
            {editing ? <textarea value={definition.summary || ''} onChange={event => setDefinition({ ...definition, summary: event.target.value })} rows={4} className="input resize-none mt-4" /> : <p className="text-sm text-body-2 mt-4 leading-relaxed">{definition.summary}</p>}
            <div className="label mt-5 mb-2">核心职责</div>{editing ? <textarea value={(definition.core_responsibilities || []).join('\n')} onChange={event => setDefinition({ ...definition, core_responsibilities: event.target.value.split('\n').filter(Boolean) })} rows={6} className="input resize-none" /> : <ol className="space-y-2">{(definition.core_responsibilities || []).map((item: string, index: number) => <li key={index} className="flex gap-2 text-sm text-body-2"><b className="text-accent-deep">{index + 1}</b>{item}</li>)}</ol>}
            <div className="grid sm:grid-cols-2 gap-5 mt-5"><div><div className="label mb-2">必备能力</div><div className="space-y-2">{capabilities.filter((item: any) => item.importance === 'required').map((item: any, index: number) => <div key={item.name || index} className="rounded-lg bg-accent/6 px-3 py-2 flex items-center justify-between gap-2"><span className="text-sm text-body-1">{item.name}</span><ConfidencePill value={item.confidence || 0} factors={item.factors} /></div>)}</div></div><div><div className="label mb-2">加分能力</div><div className="flex flex-wrap gap-1.5">{capabilities.filter((item: any) => item.importance === 'bonus').map((item: any, index: number) => <Badge key={item.name || index} tone="slate">{item.name}</Badge>)}</div><div className="label mt-5 mb-2">典型场景</div><div className="flex flex-wrap gap-1.5">{(definition.typical_scenarios || []).map((item: string) => <Badge key={item} tone="cyan">{item}</Badge>)}</div></div></div>
            {editing && <div className="mt-5 space-y-2"><input value={changeNote} onChange={event => setChangeNote(event.target.value)} className="input" placeholder="本次修订说明" /><button onClick={() => void saveDraft()} disabled={busy} className="btn-primary"><Save className="w-4 h-4" /> 保存新 revision</button></div>}
            {candidate && ['draft', 'rejected'].includes(candidate.status) && !editing && <button onClick={() => void submit()} disabled={busy} className="btn-primary mt-5">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} 提交管理员审核</button>}
            {candidate?.status === 'submitted' && <div className="mt-5 rounded-xl bg-accent/8 border border-accent/25 px-4 py-3 text-sm text-accent-deep">已提交审核，审核记录将在此候选的 reviews 中持续追加。</div>}
            {candidate?.published_job_id && <div className="mt-5"><Link to={`/jobs/${candidate.published_job_id}`} className="btn-primary">进入已发布岗位</Link></div>}
          </Card>

          <div className="space-y-4"><Card className="p-5"><div className="label mb-3">新兴度判定信号</div><div className="space-y-4">{signals.map(signal => <div key={signal.key}><div className="mb-1.5 flex items-center justify-between gap-3 text-xs"><span className="font-medium text-body-2">{signal.label}</span><b className={signal.value == null ? 'font-medium text-body-3' : 'tabular-nums text-body-1'}>{signal.value == null ? (signal.empty || '暂无数据') : `${Math.round(signal.value * 100)}%`}</b></div><Meter value={signal.value || 0} min={signal.value == null ? 0 : 3} tone={signal.value == null ? 'bg-brand-ink/12' : 'bg-accent-deep'} track="bg-brand-ink/8" /></div>)}</div></Card><Card className="p-5"><div className="flex items-center gap-2 label mb-3"><IGlobe className="w-4 h-4 text-accent-deep" /> 可追溯证据 ({runData?.evidence?.length || 0})</div><div className="space-y-2 max-h-[460px] overflow-auto">{(runData?.evidence || []).map((item: any, index: number) => <a key={index} href={item.url} target="_blank" rel="noreferrer" className="block rounded-xl bg-accent/6 hover:bg-accent/12 px-3 py-2.5"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-body-1 truncate">{item.title || item.job_title || '证据来源'}</span><ExternalLink className="w-3 h-3 text-body-3 shrink-0" /></div><p className="text-[11px] text-body-3 mt-1 line-clamp-2">{item.content || item.snippet}</p><span className="text-[10px] text-accent">{item.company || item.employer || item.provider || item.source}</span></a>)}</div></Card></div>
        </div>
      )}
    </div>
  )
}
