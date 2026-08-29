import { useEffect, useMemo, useState } from 'react'
import { Activity, CheckCircle2, ClipboardCheck, Eye, Inbox, Loader2, MessageSquareText, Play, Plus, RefreshCw, Rocket, ScrollText, XCircle } from 'lucide-react'
import { api, DailyUsage, DiscoveryCandidate, errMsg, EvolutionRunItem, FeedbackTicket, JobListItem } from '../api'
import { Badge, Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { IReview, IEvolution, IEmployer, ITalent } from '../components/icons'
import { useReadOnly } from '../hooks/useReadOnly'
import { useToast } from '../components/Toast'
import Select from '../components/Select'
import { FEEDBACK_STATUS_LABEL, FEEDBACK_STATUS_TONE } from '../presentation'

type Tab = 'users' | 'organizations' | 'candidates' | 'evolution' | 'feedback' | 'audit' | 'usage'
const TABS: { key: Tab; label: string; icon: any }[] = [
  { key: 'users', label: '用户', icon: ITalent }, { key: 'organizations', label: '组织', icon: IEmployer },
  { key: 'candidates', label: '候选审核', icon: IReview }, { key: 'evolution', label: '演化任务', icon: IEvolution },
  { key: 'feedback', label: '反馈审核', icon: MessageSquareText },
  { key: 'audit', label: '操作审计', icon: ScrollText },
  { key: 'usage', label: '每日使用', icon: Activity },
]

const EVOLUTION_TONE: Record<string, string> = {
  pending: 'slate', proposed: 'cyan', approved: 'indigo', rejected: 'rose', published: 'emerald', failed: 'rose',
}

export default function Admin() {
  const [tab, setTab] = useState<Tab>('candidates')
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [reviewing, setReviewing] = useState<DiscoveryCandidate | null>(null)
  const [comment, setComment] = useState('')
  const [publish, setPublish] = useState(false)
  const [feedbackReviewing, setFeedbackReviewing] = useState<FeedbackTicket | null>(null)
  const [recordType, setRecordType] = useState('job_version')
  const [recordId, setRecordId] = useState('')
  const [jobs, setJobs] = useState<JobListItem[]>([])
  const [evolutionJobId, setEvolutionJobId] = useState<number | null>(null)
  const [evolutionJd, setEvolutionJd] = useState('')
  const [evolutionUseWeb, setEvolutionUseWeb] = useState(false)
  const [evolutionReviewing, setEvolutionReviewing] = useState<EvolutionRunItem | null>(null)
  const [evolutionComment, setEvolutionComment] = useState('')
  const [busy, setBusy] = useState(false)
  const readOnly = useReadOnly()
  const toast = useToast()

  const load = async () => {
    setLoading(true); setError(false); setItems([])
    try {
      const result = tab === 'users' ? await api.adminUsers({ page: 1, size: 100 })
        : tab === 'organizations' ? await api.adminOrganizations({ page: 1, size: 100 })
        : tab === 'candidates' ? await api.adminCandidates({ page: 1, size: 100, status: 'submitted' })
        : tab === 'evolution' ? await api.adminEvolutionRuns({ page: 1, size: 100 })
        : tab === 'feedback' ? await api.adminFeedback({ page: 1, size: 100 })
        : tab === 'audit' ? await api.adminAuditLogs({ page: 1, size: 100 })
        : await api.adminUsageDaily({ page: 1, size: 60 })
      setItems((result as any).items || [])
    } catch { setError(true) }
    finally { setLoading(false) }
  }

  const reviewFeedback = async (action: 'triage' | 'approve' | 'reject' | 'apply') => {
    if (!feedbackReviewing) return
    if (action === 'apply' && !recordId.trim()) { toast('error', '请填写实际变更记录 ID'); return }
    setBusy(true)
    try {
      await api.reviewFeedback(feedbackReviewing.id, {
        action,
        ...(action === 'apply' ? { applied_record_type: recordType, applied_record_id: recordId.trim() } : {}),
      })
      toast('success', action === 'triage' ? '反馈已完成分诊' : action === 'approve' ? '反馈已批准' : action === 'reject' ? '反馈已驳回' : '反馈已关联实际变更')
      setFeedbackReviewing(null); setRecordId(''); await load()
    } catch (error) { toast('error', errMsg(error, '反馈审核失败')) }
    finally { setBusy(false) }
  }
  useEffect(() => { void load() }, [tab])
  useEffect(() => {
    if (tab !== 'evolution' || jobs.length) return
    api.jobs({ page: 1, size: 100 }).then(result => {
      setJobs(result.items)
      if (result.items[0]) setEvolutionJobId(result.items[0].id)
    }).catch(() => toast('error', '岗位列表加载失败'))
  }, [tab, jobs.length])

  const createEvolutionTask = async () => {
    if (!evolutionJobId || !evolutionJd.trim()) { toast('error', '请选择岗位并填写最新 JD'); return }
    setBusy(true)
    try {
      const preview = await api.previewEvolution(evolutionJobId, [evolutionJd.trim()], evolutionUseWeb)
      if (!(preview.changes || []).length) { toast('error', '当前 JD 未产生可审核的能力变化'); return }
      const evidenceBatch = {
        source: 'admin_update_task', jd_count: 1, new_jds: [evolutionJd.trim()],
        preview_stats: preview.stats || {},
      }
      const created = await api.createEvolutionRun({
        job_id: evolutionJobId, evidence_batch: evidenceBatch,
        proposed_snapshot: preview.proposed_snapshot,
        idempotency_key: `admin-ui-${evolutionJobId}-${Date.now()}`,
      })
      await api.proposeEvolutionRun(created.run.id, {
        evidence_batch: evidenceBatch, proposed_snapshot: preview.proposed_snapshot,
      })
      setEvolutionJd(''); toast('success', `演化任务 #${created.run.id} 已生成待审核提案`); await load()
    } catch (error) { toast('error', errMsg(error, '演化任务创建失败')) }
    finally { setBusy(false) }
  }

  const openEvolution = async (row: EvolutionRunItem) => {
    setBusy(true)
    try { setEvolutionReviewing(await api.adminEvolutionRun(row.id)); setEvolutionComment('') }
    catch (error) { toast('error', errMsg(error, '演化任务详情加载失败')) }
    finally { setBusy(false) }
  }

  const proposeEvolution = async () => {
    if (!evolutionReviewing) return
    setBusy(true)
    try {
      setEvolutionReviewing(await api.proposeEvolutionRun(evolutionReviewing.id))
      toast('success', '演化提案已重新生成'); await load()
    } catch (error) { toast('error', errMsg(error, '演化提案生成失败')) }
    finally { setBusy(false) }
  }

  const reviewEvolution = async (action: 'approve' | 'reject') => {
    if (!evolutionReviewing) return
    if (!evolutionComment.trim()) { toast('error', '请填写审核意见'); return }
    setBusy(true)
    try {
      setEvolutionReviewing(await api.reviewEvolutionRun(evolutionReviewing.id, {
        action, comment: evolutionComment.trim(),
      }))
      setEvolutionComment(''); toast('success', action === 'approve' ? '演化提案已批准' : '演化提案已驳回'); await load()
    } catch (error) { toast('error', errMsg(error, '演化审核失败')) }
    finally { setBusy(false) }
  }

  const publishEvolution = async () => {
    if (!evolutionReviewing) return
    if (readOnly) { toast('error', '当前公共图谱只读，不能发布新版本'); return }
    setBusy(true)
    try {
      const result = await api.publishEvolutionRun(evolutionReviewing.id)
      setEvolutionReviewing(result.run); toast('success', `岗位已发布为 v${result.job.version}`); await load()
    } catch (error) { toast('error', errMsg(error, '演化发布失败')) }
    finally { setBusy(false) }
  }

  const review = async (action: 'approve' | 'reject') => {
    if (!reviewing) return
    if (!comment.trim()) { toast('error', '请填写审核意见'); return }
    setBusy(true)
    try {
      await api.reviewCandidate(reviewing.id, { action, comment: comment.trim(), publish: action === 'approve' && publish && !readOnly })
      toast('success', action === 'approve' ? '候选已批准' : '候选已驳回')
      setReviewing(null); setComment(''); setPublish(false); await load()
    } catch (error) { toast('error', errMsg(error, '审核操作失败')) }
    finally { setBusy(false) }
  }

  const usageTotals = useMemo(() => {
    const rows = items as DailyUsage[]
    return {
      active: rows.reduce((sum, row) => sum + (row.active_users || 0), 0),
      matches: rows.reduce((sum, row) => sum + (row.matches || (row.feature === 'match' ? row.calls || 0 : 0)), 0),
      errors: rows.length ? rows.reduce((sum, row) => sum + (row.error_rate || 0), 0) / rows.length : 0,
      p95: Math.max(0, ...rows.map(row => row.p95_ms || 0)),
    }
  }, [items])

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <PageHeader icon={<IReview className="w-6 h-6" />} title="系统管理"
          subtitle="账号与组织、公共知识审核、审计与使用分析"
          action={<button onClick={() => void load()} className="btn-ghost"><RefreshCw className="w-4 h-4" /> 刷新</button>} />
      </div>
      <div className="flex gap-1.5 overflow-x-auto pb-1">{TABS.map(item => <button key={item.key} onClick={() => setTab(item.key)} className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium whitespace-nowrap ${tab === item.key ? 'bg-grad-accent text-white shadow-glow' : 'btn-ghost'}`}><item.icon className="w-4 h-4" /> {item.label}</button>)}</div>

      {tab === 'evolution' && <Card className="p-5"><div className="flex items-start justify-between gap-4 flex-wrap"><div><div className="label">新建数据更新任务</div><p className="text-sm text-body-2 mt-1">最新 JD 先生成变化提案，审核通过后才能发布岗位新版本。</p></div><Badge tone={readOnly ? 'amber' : 'emerald'}>{readOnly ? '公共图谱只读' : '允许审核发布'}</Badge></div><div className="grid lg:grid-cols-[minmax(220px,0.7fr)_minmax(0,1.7fr)] gap-3 mt-4"><Select value={String(evolutionJobId || '')} onChange={value => setEvolutionJobId(Number(value))} label="目标岗位" options={jobs.map(job => ({ value: String(job.id), label: `${job.name} · v${job.version}` }))} /><textarea value={evolutionJd} onChange={event => setEvolutionJd(event.target.value)} rows={5} className="input resize-y text-sm leading-relaxed" placeholder="最新招聘 JD" /></div><div className="flex items-center justify-between gap-3 mt-3 flex-wrap"><label className="flex items-center gap-2 text-xs text-body-2 cursor-pointer"><input type="checkbox" checked={evolutionUseWeb} onChange={event => setEvolutionUseWeb(event.target.checked)} />补充公开检索证据</label><button onClick={() => void createEvolutionTask()} disabled={busy || !evolutionJobId || !evolutionJd.trim()} className="btn-primary"><Plus className="w-4 h-4" /> 创建并生成提案</button></div></Card>}

      {tab === 'usage' && !loading && !error && <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">{[[ '活跃用户人次', usageTotals.active ], [ '匹配次数', usageTotals.matches ], [ '平均错误率', `${(usageTotals.errors * 100).toFixed(1)}%` ], [ 'P95 耗时', `${usageTotals.p95} ms` ]].map(([label, value]) => <div key={label as string} className="rounded-xl border border-line-soft/8 bg-white/75 p-4"><div className="text-xs text-body-2">{label}</div><div className="text-2xl font-extrabold text-body-1 mt-1 tabular-nums">{value}</div></div>)}</div>}

      {loading ? <Spinner /> : error ? <ErrorState text="管理数据加载失败" onRetry={load} /> : items.length === 0 ? <Card className="p-6"><EmptyState text="暂无数据" /></Card> : (
        <Card className="p-0 overflow-hidden"><div className="overflow-x-auto [-webkit-overflow-scrolling:touch]">
          {tab === 'users' && <table className="admin-table min-w-[760px]"><thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>组织</th><th>最后登录</th></tr></thead><tbody>{items.map(row => <tr key={row.id}><td>{row.id}</td><td className="font-semibold text-body-1">{row.username}</td><td><Badge tone="cyan">{row.role}</Badge></td><td><Badge tone={row.status === 'active' ? 'emerald' : 'slate'}>{row.status}</Badge></td><td>{row.organization_name || row.organization_id || '-'}</td><td>{formatDate(row.last_login_at)}</td></tr>)}</tbody></table>}
          {tab === 'organizations' && <table className="admin-table min-w-[760px]"><thead><tr><th>ID</th><th>组织</th><th>状态</th><th>成员</th><th>创建时间</th></tr></thead><tbody>{items.map(row => <tr key={row.id}><td>{row.id}</td><td className="font-semibold text-body-1">{row.name}</td><td><Badge tone={row.status === 'active' ? 'emerald' : 'slate'}>{row.status}</Badge></td><td>{row.member_count ?? '-'}</td><td>{formatDate(row.created_at)}</td></tr>)}</tbody></table>}
          {tab === 'candidates' && <table className="admin-table min-w-[760px]"><thead><tr><th>ID</th><th>岗位候选</th><th>状态</th><th>组织</th><th>版本</th><th>提交时间</th><th>操作</th></tr></thead><tbody>{items.map((row: DiscoveryCandidate) => <tr key={row.id}><td>{row.id}</td><td className="font-semibold text-body-1">{row.job_title || row.title || row.current_revision?.job_title || `候选 #${row.id}`}</td><td><Badge tone="amber">{row.status}</Badge></td><td>{row.organization_id || '-'}</td><td>r{row.revisions?.length || row.current_revision?.revision || 1}</td><td>{formatDate(row.created_at)}</td><td><button onClick={() => setReviewing(row)} className="btn-ghost !py-1.5">审核</button></td></tr>)}</tbody></table>}
          {tab === 'evolution' && <table className="admin-table min-w-[760px]"><thead><tr><th>ID</th><th>岗位</th><th>版本</th><th>变化</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{items.map((row: EvolutionRunItem) => <tr key={row.id}><td>#{row.id}</td><td className="font-semibold text-body-1">{row.job_name || `岗位 #${row.job_id}`}</td><td>v{row.from_version} → v{row.proposed_version}</td><td>{row.stats?.changes ?? 0} 项</td><td><Badge tone={EVOLUTION_TONE[row.status]}>{row.status}</Badge></td><td>{formatDate(row.updated_at)}</td><td><button onClick={() => void openEvolution(row)} className="btn-ghost !py-1.5"><Eye className="w-3.5 h-3.5" /> 查看</button></td></tr>)}</tbody></table>}
          {tab === 'feedback' && <table className="admin-table min-w-[760px]"><thead><tr><th>ID</th><th>反馈</th><th>提交人</th><th>对象</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{items.map((row: FeedbackTicket) => <tr key={row.id}><td>{row.id}</td><td><div className="font-semibold text-body-1 max-w-64 truncate">{row.category || `反馈 #${row.id}`}</div><div className="text-xs text-body-3 max-w-64 truncate mt-0.5">{row.content || '-'}</div></td><td>{row.owner_username || row.owner_user_id || '-'}{row.organization_name && <div className="text-xs text-body-3 mt-0.5">{row.organization_name}</div>}</td><td>{row.target_type || '-'}{row.target_id ? ` #${row.target_id}` : ''}</td><td><Badge tone={FEEDBACK_STATUS_TONE[row.status]}>{FEEDBACK_STATUS_LABEL[row.status] || row.status}</Badge></td><td>{formatDate(row.updated_at)}</td><td>{['submitted', 'triaged', 'approved'].includes(row.status) ? <button onClick={() => setFeedbackReviewing(row)} className="btn-ghost !py-1.5">处理</button> : row.status === 'applied' ? <span className="text-xs text-accent-deep">{row.applied_record_type} #{row.applied_record_id}</span> : <span className="text-xs text-body-3">已结束</span>}</td></tr>)}</tbody></table>}
          {tab === 'audit' && <table className="admin-table min-w-[760px]"><thead><tr><th>时间</th><th>操作人</th><th>组织</th><th>动作</th><th>对象</th><th>结果</th></tr></thead><tbody>{items.map((row, index) => <tr key={row.id || index}><td>{formatDate(row.created_at)}</td><td>{row.actor_username || row.actor_id || '-'}</td><td>{row.organization_id || '-'}</td><td className="font-medium text-body-1">{row.action}</td><td>{row.target_type}{row.target_id ? ` #${row.target_id}` : ''}</td><td><Badge tone={row.result === 'success' ? 'emerald' : 'rose'}>{row.result}</Badge></td></tr>)}</tbody></table>}
          {tab === 'usage' && <table className="admin-table min-w-[760px]"><thead><tr><th>日期</th><th>活跃用户</th><th>登录</th><th>岗位浏览</th><th>发现/匹配</th><th>错误率</th><th>P50 / P95</th></tr></thead><tbody>{items.map((row: DailyUsage, index) => <tr key={`${row.date}-${row.feature || index}`}><td className="font-semibold text-body-1">{row.date}</td><td>{row.active_users ?? '-'}</td><td>{row.logins ?? '-'}</td><td>{row.job_views ?? '-'}</td><td>{row.discovery_runs ?? 0} / {row.matches ?? (row.feature === 'match' ? row.calls : 0) ?? 0}</td><td>{row.error_rate == null ? '-' : `${(row.error_rate * 100).toFixed(1)}%`}</td><td>{row.p50_ms ?? '-'} / {row.p95_ms ?? '-'} ms</td></tr>)}</tbody></table>}
        </div></Card>
      )}

      {evolutionReviewing && <div className="fixed inset-0 z-[90] bg-brand-ink/45 backdrop-blur-sm grid place-items-center p-4" onMouseDown={event => event.target === event.currentTarget && setEvolutionReviewing(null)}><div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl bg-white border border-line-soft/8 shadow-2xl p-5"><div className="flex items-start justify-between gap-3"><div><div className="label">演化任务 · #{evolutionReviewing.id}</div><h2 className="text-xl font-extrabold text-body-1 mt-1">{evolutionReviewing.job_name || `岗位 #${evolutionReviewing.job_id}`}</h2><p className="text-xs text-body-3 mt-1">v{evolutionReviewing.from_version} → v{evolutionReviewing.proposed_version}</p></div><div className="flex items-center gap-2"><Badge tone={EVOLUTION_TONE[evolutionReviewing.status]}>{evolutionReviewing.status}</Badge><button onClick={() => setEvolutionReviewing(null)} aria-label="关闭演化任务" className="p-1.5 rounded-lg text-body-3 hover:bg-brand-ink/8 hover:text-body-1"><XCircle className="w-4 h-4" /></button></div></div><div className="grid grid-cols-3 gap-2 mt-4">{[['新增', evolutionReviewing.stats?.added || 0, 'text-accent-deep'], ['删除', evolutionReviewing.stats?.deleted || 0, 'text-danger'], ['修改', evolutionReviewing.stats?.modified || 0, 'text-warn']].map(([label, value, tone]) => <div key={label as string} className="rounded-lg bg-surface-muted px-3 py-2 text-center"><div className={`text-xl font-extrabold tabular-nums ${tone}`}>{value}</div><div className="text-xs text-body-2">{label}</div></div>)}</div>{evolutionReviewing.error && <div className="mt-4 rounded-lg border border-danger/25 bg-danger-weak px-3 py-2 text-sm text-danger">{evolutionReviewing.error}</div>}<div className="mt-4"><div className="label mb-2">提案差异</div>{(evolutionReviewing.diff || []).length === 0 ? <EmptyState text="尚未生成提案差异" /> : <div className="space-y-2 max-h-64 overflow-y-auto">{(evolutionReviewing.diff || []).map((change, index) => <div key={`${change.skill_name}-${index}`} className="flex items-start gap-3 rounded-lg bg-surface-muted px-3 py-2.5"><Badge tone={change.change_type === 'add' ? 'emerald' : change.change_type === 'delete' ? 'rose' : 'amber'}>{change.change_type}</Badge><div className="min-w-0"><div className="text-sm font-semibold text-body-1">{change.skill_name}</div><div className="text-xs text-body-2 mt-0.5">{change.reason || '能力字段变化'}</div></div></div>)}</div>}</div>{(evolutionReviewing.reviews || []).length > 0 && <div className="mt-4 border-t border-line-soft/8 pt-4"><div className="label mb-2">审核记录</div><div className="space-y-2">{(evolutionReviewing.reviews || []).map(review => <div key={review.id} className="flex items-start justify-between gap-3 text-xs"><span className="text-body-2"><b className="text-body-1">{review.action}</b> · {review.comment || '未填写意见'}</span><span className="text-body-3 shrink-0">{formatDate(review.created_at)}</span></div>)}</div></div>}{evolutionReviewing.status === 'proposed' && <textarea value={evolutionComment} onChange={event => setEvolutionComment(event.target.value)} className="input resize-none mt-4" rows={3} placeholder="填写演化审核意见（必填）" />}<div className="flex gap-2 mt-5">{['pending', 'rejected'].includes(evolutionReviewing.status) && <button onClick={() => void proposeEvolution()} disabled={busy} className="btn-primary flex-1 justify-center"><Play className="w-4 h-4" /> 生成提案</button>}{evolutionReviewing.status === 'proposed' && <><button onClick={() => void reviewEvolution('reject')} disabled={busy} className="btn-ghost flex-1 justify-center text-danger"><XCircle className="w-4 h-4" /> 驳回</button><button onClick={() => void reviewEvolution('approve')} disabled={busy} className="btn-primary flex-1 justify-center"><CheckCircle2 className="w-4 h-4" /> 批准</button></>}{evolutionReviewing.status === 'approved' && <button onClick={() => void publishEvolution()} disabled={busy || readOnly} className="btn-primary flex-1 justify-center"><Rocket className="w-4 h-4" /> {readOnly ? 'READ_ONLY=1 · 发布已阻断' : `发布 v${evolutionReviewing.proposed_version}`}</button>}{evolutionReviewing.status === 'published' && <div className="flex-1 rounded-lg bg-accent/8 px-3 py-2 text-center text-sm font-semibold text-accent-deep">版本已发布并完成对账</div>}</div></div></div>}
      {reviewing && <div className="fixed inset-0 z-[90] bg-brand-ink/45 backdrop-blur-sm grid place-items-center p-4" onMouseDown={event => event.target === event.currentTarget && setReviewing(null)}><div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl bg-white border border-line-soft/8 shadow-2xl p-5"><div className="flex items-start justify-between gap-3"><div><div className="label">候选审核</div><h2 className="text-xl font-extrabold text-body-1 mt-1">{reviewing.job_title || reviewing.title || reviewing.current_revision?.job_title || `候选 #${reviewing.id}`}</h2></div><Badge tone="amber">{reviewing.status}</Badge></div><div className="mt-4 rounded-xl bg-surface-muted p-3 text-sm text-body-2 max-h-48 overflow-auto">{reviewing.current_revision?.summary || reviewing.current_revision?.definition?.summary || '请核对候选的职责、能力和证据。'}</div><textarea value={comment} onChange={e => setComment(e.target.value)} className="input resize-none mt-4" rows={4} placeholder="填写审核意见（必填）" /><label className="flex items-start gap-2 mt-3 text-xs text-body-2"><input type="checkbox" checked={publish} disabled={readOnly} onChange={e => setPublish(e.target.checked)} className="mt-0.5 accent-accent" /><span>批准后同时发布为公共岗位 v1{readOnly ? '（当前公共图谱只读，不可发布）' : ''}</span></label><div className="flex gap-2 mt-5"><button onClick={() => void review('reject')} disabled={busy} className="btn-ghost flex-1 justify-center text-danger"><XCircle className="w-4 h-4" /> 驳回</button><button onClick={() => void review('approve')} disabled={busy} className="btn-primary flex-1 justify-center">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />} 批准</button></div></div></div>}
      {feedbackReviewing && <div className="fixed inset-0 z-[90] bg-brand-ink/45 backdrop-blur-sm grid place-items-center p-4" onMouseDown={event => event.target === event.currentTarget && setFeedbackReviewing(null)}><div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl bg-white border border-line-soft/8 shadow-2xl p-5"><div className="flex items-start justify-between gap-3"><div><div className="label">反馈审核 · #{feedbackReviewing.id}</div><h2 className="text-xl font-extrabold text-body-1 mt-1">{feedbackReviewing.category || '知识更新反馈'}</h2><p className="text-xs text-body-3 mt-1">{feedbackReviewing.owner_username || `用户 #${feedbackReviewing.owner_user_id}`} · revision {feedbackReviewing.current_revision || 1}</p></div><Badge tone={FEEDBACK_STATUS_TONE[feedbackReviewing.status]}>{FEEDBACK_STATUS_LABEL[feedbackReviewing.status]}</Badge></div><div className="mt-4 rounded-xl bg-surface-muted p-4 text-sm text-body-2 whitespace-pre-wrap break-words">{feedbackReviewing.content || '未填写详细说明'}</div>{feedbackReviewing.status === 'approved' && <div className="mt-4 p-4 rounded-xl border border-accent/30 bg-accent/8"><div className="flex items-center gap-2 text-sm font-semibold text-accent-deep"><Inbox className="w-4 h-4" /> 关联实际知识变更</div><div className="grid sm:grid-cols-[minmax(0,1fr)_140px] gap-3 mt-3"><Select value={recordType} onChange={setRecordType} options={[{ value: 'job_version', label: '岗位版本' }, { value: 'evolution_run', label: '演化任务' }, { value: 'job_candidate_revision', label: '岗位候选版本' }, { value: 'skill_alias', label: '技能词典项' }, { value: 'crawl_batch', label: '数据采集批次' }]} /><input value={recordId} onChange={event => setRecordId(event.target.value)} className="input" inputMode="numeric" placeholder="记录 ID" /></div><p className="text-xs text-accent-deep mt-2">系统会校验记录确实存在，再将工单标记为已应用。</p></div>}<div className="flex gap-2 mt-5">{feedbackReviewing.status === 'submitted' && <button onClick={() => void reviewFeedback('triage')} disabled={busy} className="btn-primary flex-1 justify-center">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ClipboardCheck className="w-4 h-4" />} 接收并分诊</button>}{feedbackReviewing.status === 'triaged' && <><button onClick={() => void reviewFeedback('reject')} disabled={busy} className="btn-ghost flex-1 justify-center text-danger"><XCircle className="w-4 h-4" /> 驳回</button><button onClick={() => void reviewFeedback('approve')} disabled={busy} className="btn-primary flex-1 justify-center">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />} 批准</button></>}{feedbackReviewing.status === 'approved' && <button onClick={() => void reviewFeedback('apply')} disabled={busy} className="btn-primary flex-1 justify-center">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />} 确认已应用</button>}</div></div></div>}
    </div>
  )
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '-'
}
