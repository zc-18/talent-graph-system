import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CalendarClock, RotateCcw, Target } from 'lucide-react'
import { api, errMsg, MatchHistoryItem } from '../api'
import { Badge, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import { useToast } from '../components/Toast'

export default function History() {
  const [items, setItems] = useState<MatchHistoryItem[]>([])
  const [selected, setSelected] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true); setError(false)
    api.matchHistory({ page: 1, size: 50 }).then(data => setItems(data.items || []))
      .catch(() => setError(true)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const open = async (item: MatchHistoryItem) => {
    try { setSelected(await api.matchHistoryDetail(item.id)) }
    catch (error) { toast('error', errMsg(error, '历史详情加载失败')) }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow"><CalendarClock className="w-5 h-5 text-white" /></div>
          <div><h1 className="text-2xl font-extrabold text-slate-900">个人匹配历史</h1><p className="text-sm text-slate-500">固定引用当时的岗位版本，可重新打开结果与学习路径</p></div>
        </div>
        <Link to="/match" className="btn-primary"><Target className="w-4 h-4" /> 新建匹配</Link>
      </div>

      {loading ? <Spinner /> : error ? <ErrorState text="匹配历史加载失败" onRetry={load} /> : items.length === 0 ? (
        <Card className="p-6"><EmptyState text="暂无匹配历史" hint="完成一次人岗匹配后，结果会保存在这里" /></Card>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px] gap-5">
          <div className="space-y-3">
            {items.map(item => (
              <button key={item.id} onClick={() => open(item)} className="w-full text-left rounded-xl border border-slate-200 bg-white/75 p-4 hover:border-sky-300 hover:bg-white transition">
                <div className="flex items-start justify-between gap-3">
                  <div><div className="font-bold text-slate-800">{item.job_name}</div><div className="text-xs text-slate-400 mt-1">{new Date(item.created_at).toLocaleString()} · 岗位版本 v{item.job_version}</div></div>
                  <div className="flex items-center gap-3"><span className="text-2xl font-extrabold text-slate-900 tabular-nums">{Math.round(item.overall_score)}</span><ArrowRight className="w-4 h-4 text-slate-300" /></div>
                </div>
                <div className="flex items-center gap-2 mt-3"><Badge tone="cyan">{item.level || '匹配完成'}</Badge><Badge tone="slate">{item.top_gaps?.length || 0} 个关键缺口</Badge></div>
              </button>
            ))}
          </div>
          <Card className="p-5 xl:sticky xl:top-6 h-fit">
            {!selected ? <EmptyState text="选择一条历史查看快照" /> : (
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3"><div><div className="label">历史快照</div><h2 className="font-extrabold text-xl text-slate-900 mt-1">{selected.job_name || selected.job?.name}</h2></div><Badge tone="cyan">v{selected.job_version || selected.job?.version}</Badge></div>
                <div className="rounded-xl bg-sky-50/70 p-4"><div className="text-xs text-slate-500">当时综合匹配度</div><div className="text-3xl font-extrabold text-slate-900 mt-1">{Math.round(selected.overall_score ?? selected.result?.overall_score ?? 0)}</div></div>
                <div><div className="label mb-2">Top 关键缺口</div><div className="space-y-2">{(selected.top_gaps || selected.result?.missing_required || []).slice(0, 10).map((gap: any, index: number) => <div key={gap.name || gap.skill || index} className="flex justify-between gap-3 text-sm border-b border-slate-100 pb-2"><span className="text-slate-700">{gap.name || gap.skill}</span><Badge tone={gap.importance === 'required' ? 'rose' : 'amber'}>{gap.priority || '待提升'}</Badge></div>)}</div></div>
                <Link to="/match" state={{ jobId: selected.job_id || selected.job?.id }} className="btn-ghost w-full justify-center"><RotateCcw className="w-4 h-4" /> 按此岗位再次匹配</Link>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
