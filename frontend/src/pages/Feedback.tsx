import { FormEvent, useEffect, useState } from 'react'
import { CheckCircle2, Loader2, MessageSquareText, Send } from 'lucide-react'
import { api, errMsg, FeedbackTicket } from '../api'
import { Badge, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import Select from '../components/Select'
import { useToast } from '../components/Toast'

const STATUS: Record<string, string> = { submitted: '已提交', triaged: '已分诊', approved: '已批准', rejected: '已驳回', applied: '已应用' }

export default function Feedback() {
  const [items, setItems] = useState<FeedbackTicket[]>([])
  const [type, setType] = useState('job_profile')
  const [subject, setSubject] = useState('')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true); setError(false)
    api.feedbackList({ page: 1, size: 50 }).then(data => setItems(data.items || []))
      .catch(() => setError(true)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!subject.trim() || content.trim().length < 10) { toast('error', '请填写标题和至少 10 个字的说明'); return }
    setSending(true)
    try {
      await api.feedback({ target_type: type, target_id: null, category: subject.trim(), content: content.trim(), evidence: [] })
      setSubject(''); setContent(''); toast('success', '反馈已进入审核队列'); load()
    } catch (error) { toast('error', errMsg(error, '反馈提交失败')) }
    finally { setSending(false) }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3"><div className="w-11 h-11 rounded-xl bg-grad-violet grid place-items-center shadow-glow"><MessageSquareText className="w-5 h-5 text-white" /></div><div><h1 className="text-2xl font-extrabold text-slate-900">反馈与知识更新</h1><p className="text-sm text-slate-500">反馈须经审核才能影响公共岗位知识，全程可跟踪</p></div></div>
      <div className="grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)] gap-5 items-start">
        <Card className="p-5">
          <form onSubmit={submit} className="space-y-4">
            <div><div className="label mb-2">反馈类型</div><Select value={type} onChange={setType} options={[{ value: 'job_profile', label: '岗位画像' }, { value: 'skill', label: '能力项' }, { value: 'evidence', label: '证据来源' }, { value: 'match', label: '匹配结果' }, { value: 'team_gap', label: '团队缺口' }]} /></div>
            <label className="block"><span className="label block mb-2">标题</span><input className="input" value={subject} onChange={e => setSubject(e.target.value)} placeholder="简要概括需要修正的内容" /></label>
            <label className="block"><span className="label block mb-2">详细说明</span><textarea className="input resize-none" rows={7} value={content} onChange={e => setContent(e.target.value)} placeholder="请说明当前问题、建议结论和可核对的依据" /></label>
            <button disabled={sending} className="btn-primary w-full justify-center">{sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} 提交反馈</button>
          </form>
        </Card>
        <div>
          <div className="label mb-3">我的反馈进度</div>
          {loading ? <Spinner /> : error ? <ErrorState text="反馈记录加载失败" onRetry={load} /> : items.length === 0 ? <Card className="p-5"><EmptyState text="暂无反馈记录" /></Card> : (
            <div className="space-y-3">{items.map(item => <div key={item.id} className="rounded-xl border border-slate-200 bg-white/75 p-4"><div className="flex items-start justify-between gap-3"><div><div className="font-semibold text-slate-800">{item.subject || item.category || `反馈 #${item.id}`}</div><div className="text-xs text-slate-400 mt-1">{new Date(item.created_at).toLocaleString()}</div></div><Badge tone={item.status === 'applied' ? 'emerald' : item.status === 'rejected' ? 'rose' : 'cyan'}>{STATUS[item.status] || item.status}</Badge></div>{item.content && <p className="text-sm text-slate-500 mt-3 line-clamp-2">{item.content}</p>}{item.status === 'applied' && <div className="flex items-center gap-1.5 text-xs text-emerald-600 mt-3"><CheckCircle2 className="w-3.5 h-3.5" /> 已关联实际知识变更</div>}</div>)}</div>
          )}
        </div>
      </div>
    </div>
  )
}
