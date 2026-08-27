import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ExternalLink, GitBranch, Loader2, Pencil, Save, Search, Send, X } from 'lucide-react'
import { ISparkle, IGlobe, ILightning } from '../components/icons'
import { api, errMsg } from '../api'
import { Badge, Card, ConfidencePill, EmptyState } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'
import { useAuth } from '../auth'

const TRACKS = [
  { value: '', label: '自动判定轨道' }, { value: 'software', label: '软件' }, { value: 'hardware', label: '硬件' },
  { value: 'algorithm', label: '算法' }, { value: 'data', label: '数据' }, { value: 'ops', label: '运维' }, { value: 'product', label: '产品' },
]
const SENIORITIES = [{ value: '', label: '不限级别' }, { value: 'junior', label: '初级' }, { value: 'middle', label: '中级' }, { value: 'senior', label: '高级' }]
const RECRUITMENT = [{ value: '', label: '校招/社招' }, { value: 'campus', label: '校招' }, { value: 'social', label: '社招' }]

export default function Discovery() {
  const auth = useAuth()
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

  useEffect(() => { api.seeds().then(data => setSeeds(data.seeds || [])).catch(() => setSeeds([])) }, [])

  const run = async (value = keyword, selectedTrack = track) => {
    if (!value.trim()) return
    setLoading(true); setResult(null); setDefinition(null); setEditing(false)
    try {
      const response = auth.isAuthenticated
        ? await api.discoveryRun({ keyword: value.trim(), track: selectedTrack || undefined, seniority: seniority || undefined, recruitment_type: recruitmentType || undefined })
        : await api.discover(value.trim(), false)
      setResult(response)
      setDefinition(response.candidate?.definition || response.definition || null)
    } catch (error) { toast('error', errMsg(error, '发现任务失败，请检查网络检索与模型服务')) }
    finally { setLoading(false) }
  }

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
  const signals = useMemo(() => Object.entries(runData?.signals || {}).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value)), [runData])

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3"><div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow"><ISparkle className="w-6 h-6 text-white" /></div><div><h1 className="text-2xl font-extrabold text-slate-900">新岗位发现与定义</h1><p className="text-sm text-slate-500">成熟职业否决 · 结构化消歧 · 多雇主证据 · 候选审核发布</p></div></div>

      <Card className="p-5 space-y-3">
        <div className="flex flex-col sm:flex-row gap-2"><div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-white/80 border border-slate-200 flex-1"><Search className="w-4 h-4 text-slate-400" /><input value={keyword} onChange={event => setKeyword(event.target.value)} onKeyDown={event => event.key === 'Enter' && void run()} className="bg-transparent outline-none text-sm flex-1 min-w-0" placeholder="输入岗位名称，如：软件系统测试工程师" /></div><button onClick={() => void run()} disabled={loading} className="btn-primary justify-center">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ILightning className="w-4 h-4" />} 检索并判定</button></div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2"><Select value={track} onChange={setTrack} options={TRACKS} label="岗位轨道" /><Select value={seniority} onChange={setSeniority} options={SENIORITIES} label="岗位级别" /><Select value={recruitmentType} onChange={setRecruitmentType} options={RECRUITMENT} label="招聘类型" /></div>
        <div className="flex flex-wrap gap-1.5">{seeds.slice(0, 12).map(seed => <button key={seed} onClick={() => { setKeyword(seed); void run(seed) }} className="chip border bg-white/70 border-slate-200 text-slate-600 hover:border-sky-300">{seed}</button>)}</div>
        {!auth.isAuthenticated && <div className="text-xs text-slate-500">当前为匿名预览，<Link to="/login" className="text-accent font-semibold hover:underline">登录</Link>后可保存候选草稿、追加修订并提交审核。</div>}
      </Card>

      {loading && <Card className="p-10 text-center"><Loader2 className="w-8 h-8 animate-spin mx-auto text-accent" /><p className="text-sm text-slate-500 mt-3">正在进行成熟岗位否决、历史新颖性与多雇主证据核验…</p></Card>}
      {!loading && !result && <Card className="p-7"><div className="grid sm:grid-cols-3 gap-4">{[[IGlobe, '不同雇主实体', '平台仅是传播渠道，同公司跨平台不重复计数'], [AlertTriangle, '成熟职业否决', 'Java、前端、测试等成熟岗位转入演化'], [Pencil, '草稿可修订', '修订与审核记录只追加、不覆盖']].map(([Icon, title, text]: any) => <div key={title} className="border-t-2 border-sky-200 pt-4"><Icon className="w-5 h-5 text-accent" /><div className="font-bold text-slate-800 mt-3">{title}</div><p className="text-xs text-slate-500 mt-1 leading-relaxed">{text}</p></div>)}</div></Card>}

      {!loading && classification === 'ESTABLISHED' && <Card className="p-6 border-emerald-200"><div className="flex items-start gap-3"><CheckCircle2 className="w-6 h-6 text-emerald-600 shrink-0" /><div className="flex-1"><Badge tone="emerald">既有岗位</Badge><h2 className="text-xl font-extrabold text-slate-900 mt-2">{matchedJob?.name || keyword}</h2><p className="text-sm text-slate-500 mt-2">命中成熟职业词典或正式岗位别名，不会创建重复候选。</p><div className="flex flex-wrap gap-2 mt-4">{matchedJob?.id && <Link to={`/jobs/${matchedJob.id}`} className="btn-primary">查看岗位画像</Link>}{matchedJob?.id && <Link to="/evolution" state={{ jobId: matchedJob.id }} className="btn-ghost"><GitBranch className="w-4 h-4" /> 查看演化</Link>}</div></div></div></Card>}

      {!loading && classification === 'AMBIGUOUS' && <Card className="p-6 border-amber-200"><div className="flex items-start gap-3"><AlertTriangle className="w-6 h-6 text-amber-600 shrink-0" /><div className="flex-1"><Badge tone="amber">需要消歧</Badge><h2 className="text-xl font-extrabold text-slate-900 mt-2">请明确岗位轨道</h2><p className="text-sm text-slate-500 mt-2">当前输入可能混合软件、硬件或行业测试语料，选择后重新判定。</p><div className="flex flex-wrap gap-2 mt-4">{candidates.map((item: any, index: number) => { const selectedTrack = typeof item === 'string' ? '' : item.track || item.value || ''; const candidateTitle = typeof item === 'string' ? item : item.canonical_title || keyword; return <button key={`${selectedTrack}-${candidateTitle}-${index}`} onClick={() => { setTrack(selectedTrack); void run(candidateTitle, selectedTrack) }} className="btn-ghost">{typeof item === 'string' ? item : item.label || item.canonical_title || item.track}</button> })}</div></div></div></Card>}

      {!loading && classification === 'NEW' && definition && (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5 items-start">
          <Card className="p-6">
            <div className="flex items-start justify-between gap-3 flex-wrap"><div><div className="flex items-center gap-2"><Badge tone="amber">新岗位候选</Badge>{candidate && <Badge tone="cyan">{candidate.status} · r{candidate.current_revision_number || candidate.current_revision?.revision}</Badge>}</div>{editing ? <input value={definition.job_title || ''} onChange={event => setDefinition({ ...definition, job_title: event.target.value })} className="input mt-3 text-lg font-bold" /> : <h2 className="text-2xl font-extrabold text-slate-900 mt-3">{definition.job_title}</h2>}</div>{candidate && ['draft', 'rejected'].includes(candidate.status) && <button onClick={() => setEditing(value => !value)} className="btn-ghost">{editing ? <X className="w-4 h-4" /> : <Pencil className="w-4 h-4" />} {editing ? '取消' : '优化草稿'}</button>}</div>
            {editing ? <textarea value={definition.summary || ''} onChange={event => setDefinition({ ...definition, summary: event.target.value })} rows={4} className="input resize-none mt-4" /> : <p className="text-sm text-slate-600 mt-4 leading-relaxed">{definition.summary}</p>}
            <div className="label mt-5 mb-2">核心职责</div>{editing ? <textarea value={(definition.core_responsibilities || []).join('\n')} onChange={event => setDefinition({ ...definition, core_responsibilities: event.target.value.split('\n').filter(Boolean) })} rows={6} className="input resize-none" /> : <ol className="space-y-2">{(definition.core_responsibilities || []).map((item: string, index: number) => <li key={index} className="flex gap-2 text-sm text-slate-600"><b className="text-accent">{index + 1}</b>{item}</li>)}</ol>}
            <div className="grid sm:grid-cols-2 gap-5 mt-5"><div><div className="label mb-2">必备能力</div><div className="space-y-2">{capabilities.filter((item: any) => item.importance === 'required').map((item: any, index: number) => <div key={item.name || index} className="rounded-lg bg-sky-50/70 px-3 py-2 flex items-center justify-between gap-2"><span className="text-sm text-slate-700">{item.name}</span><ConfidencePill value={item.confidence || 0} factors={item.factors} /></div>)}</div></div><div><div className="label mb-2">加分能力</div><div className="flex flex-wrap gap-1.5">{capabilities.filter((item: any) => item.importance === 'bonus').map((item: any, index: number) => <Badge key={item.name || index} tone="slate">{item.name}</Badge>)}</div><div className="label mt-5 mb-2">典型场景</div><div className="flex flex-wrap gap-1.5">{(definition.typical_scenarios || []).map((item: string) => <Badge key={item} tone="cyan">{item}</Badge>)}</div></div></div>
            {editing && <div className="mt-5 space-y-2"><input value={changeNote} onChange={event => setChangeNote(event.target.value)} className="input" placeholder="本次修订说明" /><button onClick={() => void saveDraft()} disabled={busy} className="btn-primary"><Save className="w-4 h-4" /> 保存新 revision</button></div>}
            {candidate && ['draft', 'rejected'].includes(candidate.status) && !editing && <button onClick={() => void submit()} disabled={busy} className="btn-primary mt-5">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} 提交管理员审核</button>}
            {candidate?.status === 'submitted' && <div className="mt-5 rounded-xl bg-cyan-50 border border-cyan-100 px-4 py-3 text-sm text-cyan-700">已提交审核，审核记录将在此候选的 reviews 中持续追加。</div>}
            {candidate?.published_job_id && <div className="mt-5"><Link to={`/jobs/${candidate.published_job_id}`} className="btn-primary">进入已发布岗位</Link></div>}
          </Card>

          <div className="space-y-4"><Card className="p-5"><div className="label mb-3">新兴度判定信号</div>{signals.length === 0 ? <EmptyState text="暂无信号明细" /> : <div className="space-y-2">{signals.map(([key, value]) => <div key={key} className="flex justify-between gap-2 text-xs border-b border-slate-100 pb-2"><span className="text-slate-500">{key}</span><b className="text-slate-700">{String(value)}</b></div>)}</div>}</Card><Card className="p-5"><div className="flex items-center gap-2 label mb-3"><IGlobe className="w-4 h-4 text-cyan-600" /> 可追溯证据 ({runData?.evidence?.length || 0})</div><div className="space-y-2 max-h-[460px] overflow-auto">{(runData?.evidence || []).map((item: any, index: number) => <a key={index} href={item.url} target="_blank" rel="noreferrer" className="block rounded-xl bg-sky-50/70 hover:bg-sky-100 px-3 py-2.5"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-slate-700 truncate">{item.title || item.job_title || '证据来源'}</span><ExternalLink className="w-3 h-3 text-slate-400 shrink-0" /></div><p className="text-[11px] text-slate-400 mt-1 line-clamp-2">{item.content || item.snippet}</p><span className="text-[10px] text-accent">{item.company || item.employer || item.provider || item.source}</span></a>)}</div></Card></div>
        </div>
      )}
    </div>
  )
}
