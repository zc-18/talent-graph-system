import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Sparkles, PlusCircle } from 'lucide-react'
import { IJob, IEvolution, IGraph, IMatch } from '../components/icons'
import { api, JobListItem, CATEGORY_COLORS } from '../api'
import { Card, Spinner, ConfidencePill, Badge, EmptyState, ErrorState, PageHeader, Pagination } from '../components/ui'
import Select from '../components/Select'
import { ConfidenceMeta } from '../components/ConfidenceMeta'

const LEVEL_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' }
const PAGE_SIZE = 9

export default function Jobs() {
  const [items, setItems] = useState<JobListItem[]>([])
  const [cats, setCats] = useState<string[]>([])
  const [cat, setCat] = useState('全部')
  const [q, setQ] = useState('')
  const [submittedQ, setSubmittedQ] = useState('')
  const [onlyNew, setOnlyNew] = useState(false)
  const [track, setTrack] = useState('全部')
  const [seniority, setSeniority] = useState('全部')
  const [recruitmentType, setRecruitmentType] = useState('全部')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [reloadTick, setReloadTick] = useState(0)
  const nav = useNavigate()

  useEffect(() => { api.categories().then(d => setCats(['全部', ...d.categories])).catch(() => {}) }, [])
  const load = useCallback(() => {
    setLoading(true); setError(false)
    const params: any = { page, size: PAGE_SIZE }
    if (cat !== '全部') params.category = cat
    if (submittedQ) params.q = submittedQ
    if (onlyNew) params.is_new = true
    if (track !== '全部') params.track = track
    if (seniority !== '全部') params.seniority = seniority
    if (recruitmentType !== '全部') params.recruitment_type = recruitmentType
    api.jobs(params).then(d => { setItems(d.items); setTotal(d.total); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [page, cat, submittedQ, onlyNew, track, seniority, recruitmentType, reloadTick])
  useEffect(load, [load])

  const updateFilter = (setter: (value: string) => void, value: string) => {
    setPage(1)
    setter(value)
  }
  const submitSearch = () => {
    const next = q.trim()
    setPage(1)
    if (next === submittedQ && page === 1) setReloadTick(tick => tick + 1)
    else setSubmittedQ(next)
  }
  const retry = () => setReloadTick(tick => tick + 1)

  return (
    <div className="space-y-5">
      <PageHeader icon={<IJob className="w-6 h-6" />} title="岗位库管理"
        subtitle={`${total} 个岗位 · 支持检索与人工优化`} />

      {/* 窄屏：2 列网格（搜索框独占整行），控件铺满栅格避免定宽挤出横向滚动；
          sm 起切回原来的定宽 flex 排布。纯断点驱动，不做 JS 宽度判断。 */}
      <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
        <div className="col-span-2 flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-white/80 border border-line-soft/8 sm:flex-1 sm:min-w-[220px] focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/15 transition">
          <Search className="w-4 h-4 shrink-0 text-body-3" />
          <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && submitSearch()}
            placeholder="搜索岗位名称…" className="bg-transparent text-sm outline-none flex-1 min-w-0 text-body-1 placeholder:text-body-3" />
        </div>
        <Select value={cat} onChange={value => updateFilter(setCat, value)} options={cats} className="w-full sm:w-44" />
        <Select value={track} onChange={value => updateFilter(setTrack, value)} className="w-full sm:w-36" label="岗位轨道" options={[
          { value: '全部', label: '全部轨道' }, { value: 'software', label: '软件' }, { value: 'hardware', label: '硬件' },
          { value: 'algorithm', label: '算法' }, { value: 'data', label: '数据' }, { value: 'ops', label: '运维' }, { value: 'product', label: '产品' },
        ]} />
        <Select value={seniority} onChange={value => updateFilter(setSeniority, value)} className="w-full sm:w-32" label="岗位级别" options={[
          { value: '全部', label: '全部级别' }, { value: 'junior', label: '初级' }, { value: 'middle', label: '中级' }, { value: 'senior', label: '高级' },
        ]} />
        <Select value={recruitmentType} onChange={value => updateFilter(setRecruitmentType, value)} className="w-full sm:w-32" label="招聘类型" options={[
          { value: '全部', label: '校招/社招' }, { value: 'campus', label: '校招' }, { value: 'social', label: '社招' }, { value: 'mixed', label: '混合' },
        ]} />
        <button onClick={() => { setPage(1); setOnlyNew(v => !v) }}
          className={`${onlyNew ? 'btn-primary' : 'btn-ghost'} !px-3 whitespace-nowrap`}>
          <Sparkles className="w-4 h-4 shrink-0" /> 仅新兴岗位
        </button>
        <button onClick={() => nav('/discovery')} className="btn-ghost !px-3 whitespace-nowrap">
          <PlusCircle className="w-4 h-4 shrink-0" /> 发现新岗位
        </button>
      </div>

      {loading ? <Spinner /> : error ? <ErrorState text="岗位列表加载失败" onRetry={retry} /> : items.length === 0 ? <EmptyState text="未找到匹配的岗位" hint="试试更换分类或关键词" /> : (
        <>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 sm:gap-4">
          {items.map((j, i) => (
            <Card key={j.id} delay={i * 0.02} hover className="p-5 cursor-pointer group"
              >
              <div onClick={() => nav(`/jobs/${j.id}`)}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: CATEGORY_COLORS[j.category] || '#64748B' }} />
                    <h3 className="font-bold text-body-1 truncate group-hover:text-body-1">{j.name}</h3>
                  </div>
                  {j.is_new && <Badge tone="amber">新兴</Badge>}
                </div>
                <p className="text-xs text-body-2 mt-2 line-clamp-2 min-h-[32px]">{j.summary}</p>
                {j.core_capabilities && j.core_capabilities.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-3">{j.core_capabilities.slice(0, 10).map(capability => <Badge key={capability} tone="slate">{capability}</Badge>)}</div>
                )}
                <div className="flex items-center justify-between mt-3">
                  <div className="flex gap-1.5">
                    <Badge tone="indigo">{j.category}</Badge>
                    <Badge tone="slate">{LEVEL_LABEL[j.level] || j.level}</Badge>
                  </div>
                  <ConfidencePill value={j.confidence} />
                </div>
                <div className="mt-2 min-w-0">
                  <ConfidenceMeta asOf={j.confidence_as_of} delta={j.confidence_delta} compact />
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 border-t border-line-soft/8 pt-3 text-[11px] text-body-3">
                  <span className="flex items-center gap-1.5">{j.required_count} 个岗位契约能力簇</span>
                  {/* evidence_count 是 active 能力项的 source_count 之和，即「多少条 JD 支撑了
                      这个岗位的能力集」，不是证据表的行数。原来写「证据 N」与详情页
                      「留存证据 M 条」口径打架，两处统一成 JD 支撑。 */}
                  <span title="该岗位能力集累计获得的真实 JD 支撑数">雇主 {j.employer_count || 0} · JD 支撑 {j.evidence_count} · v{j.version}</span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-line-soft/8">
                <button onClick={() => nav('/match', { state: { jobId: j.id } })} className="btn-ghost !px-2 !py-2 text-xs"><IMatch className="w-3.5 h-3.5" /> 匹配</button>
                <button onClick={() => nav('/panorama', { state: { jobId: j.id } })} className="btn-ghost !px-2 !py-2 text-xs"><IGraph className="w-3.5 h-3.5" /> 图谱</button>
                <button onClick={() => nav('/evolution', { state: { jobId: j.id } })} className="btn-ghost !px-2 !py-2 text-xs"><IEvolution className="w-3.5 h-3.5" /> 演化</button>
              </div>
            </Card>
          ))}
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} label="岗位库分页" />
        </>
      )}
    </div>
  )
}
