import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Network, Filter, Maximize2 } from 'lucide-react'
import { api, GraphData, CATEGORY_COLORS } from '../api'
import { Card, Spinner, Badge } from '../components/ui'
import Select from '../components/Select'

export default function Panorama() {
  const [data, setData] = useState<GraphData | null>(null)
  const [cats, setCats] = useState<string[]>([])
  const [levels, setLevels] = useState<string[]>([])
  const [cat, setCat] = useState('全部')
  const [level, setLevel] = useState('全部')
  const [minConf, setMinConf] = useState(0)
  const [sel, setSel] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const chartRef = useRef<any>(null)

  useEffect(() => {
    api.categories().then(d => { setCats(['全部', ...d.categories]); setLevels(['全部', ...d.levels]) })
  }, [])
  useEffect(() => {
    setLoading(true)
    api.panorama(cat, level, minConf).then(d => { setData(d); setLoading(false) })
  }, [cat, level, minConf])

  const option = useMemo(() => {
    if (!data) return {}
    const hex2rgba = (hex: string, a: number) => {
      const m = hex.replace('#', '')
      const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16)
      return `rgba(${r},${g},${b},${a})`
    }
    // 浅色渐变调色板（每个技术栈一对 [浅, 深]），更丰富、更柔和
    const CAT_GRAD: Record<string, [string, string]> = {
      人工智能: ['#A5B4FC', '#6366F1'],
      大数据: ['#7DD3FC', '#0EA5E9'],
      物联网: ['#6EE7B7', '#10B981'],
      智能系统: ['#FCD34D', '#F59E0B'],
      云计算与工程: ['#D8B4FE', '#A855F7'],
      数据工程: ['#F9A8D4', '#EC4899'],
      其他: ['#CBD5E1', '#94A3B8'],
    }
    const mixWhite = (hex: string, t: number) => {
      const m = hex.replace('#', '')
      let r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16)
      r = Math.round(r + (255 - r) * t); g = Math.round(g + (255 - g) * t); b = Math.round(b + (255 - b) * t)
      return `rgb(${r},${g},${b})`
    }
    // 竖向线性渐变（扁平"硬币"质感，避免发光小球的廉价感）
    const linearV = (c0: string, c1: string) => ({
      type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
      colorStops: [{ offset: 0, color: c0 }, { offset: 1, color: c1 }],
    })
    const nodes = data.nodes.map(n => {
      const isJob = n.type === 'job'
      const [light, dark] = CAT_GRAD[n.category] || CAT_GRAD['其他']
      return {
        id: n.id, name: n.name,
        symbolSize: isJob ? Math.max(38, Math.min(58, n.value + 10)) : Math.max(13, Math.min(26, n.value - 1)),
        category: isJob ? 0 : 1,
        itemStyle: isJob
          ? {
              // 岗位：扁平硬币 + 通透白描边 + 同色柔光晕
              color: linearV(mixWhite(dark, 0.30), dark),
              borderColor: n.is_new ? '#F59E0B' : 'rgba(255,255,255,0.92)',
              borderWidth: n.is_new ? 2.5 : 2,
              shadowBlur: 26, shadowColor: hex2rgba(dark, 0.33),
            }
          : {
              // 技能：极简扁平半透明圆点，airy 不喧宾夺主
              color: mixWhite(light, 0.40),
              opacity: 0.92,
              borderColor: '#ffffff', borderWidth: 1.5,
              shadowBlur: 9, shadowColor: hex2rgba(light, 0.4),
            },
        label: {
          show: true,
          position: isJob ? 'bottom' : 'right',
          distance: isJob ? 8 : 6,
          color: isJob ? '#0F172A' : '#7C8BA3',
          fontSize: isJob ? 12 : 10,
          fontWeight: isJob ? 700 : 500,
          backgroundColor: isJob ? 'rgba(255,255,255,0.85)' : 'transparent',
          padding: isJob ? [3, 7] : 0, borderRadius: 6,
        },
        _raw: n,
      }
    })
    const links = data.edges.map(e => ({
      source: e.source, target: e.target,
      // 禁用边的悬浮高亮：避免鼠标划过密集连线时反复触发聚焦导致的"闪屏"
      emphasis: { disabled: true },
      lineStyle: {
        color: e.importance === 'required' ? 'rgba(129,140,248,0.32)' : 'rgba(148,163,184,0.14)',
        width: e.importance === 'required' ? 1.3 : 0.6, curveness: 0.14,
      },
    }))
    return {
      tooltip: {
        backgroundColor: 'rgba(255,255,255,0.97)', borderColor: 'rgba(2,6,23,0.08)',
        borderWidth: 1, padding: [8, 12],
        extraCssText: 'box-shadow:0 8px 24px -8px rgba(37,99,235,0.25);border-radius:10px;',
        textStyle: { color: '#1E293B' },
        formatter: (p: any) => p.dataType === 'node'
          ? `<b>${p.data.name}</b><br/>${p.data._raw.type === 'job' ? '岗位 · ' + (p.data._raw.category || '') : '技能点 · 关联岗位 ' + (p.data._raw.degree || 0)}`
          : '',
      },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        force: { repulsion: 560, edgeLength: [120, 260], gravity: 0.10, friction: 0.18 },
        categories: [{ name: '岗位' }, { name: '技能点' }],
        labelLayout: { hideOverlap: true },
        // 平滑状态切换 + 仅节点触发柔和聚焦，杜绝鼠标划过连线时的"屏闪"
        stateAnimation: { duration: 280, easing: 'cubicOut' },
        emphasis: { focus: 'adjacency',
          label: { show: true }, lineStyle: { width: 2, color: 'rgba(129,140,248,0.6)' } },
        blur: { itemStyle: { opacity: 0.65 }, lineStyle: { opacity: 0.08 }, label: { opacity: 0.45 } },
        data: nodes, links, scaleLimit: { min: 0.3, max: 4 }, center: ['50%', '50%'], zoom: 0.78,
      }],
    }
  }, [data])

  const onEvents = {
    click: (p: any) => { if (p.dataType === 'node') setSel(p.data._raw) },
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl bg-grad-accent grid place-items-center shadow-glow">
            <Network className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold text-slate-900">新一代信息技术岗位全景图谱</h1>
            <p className="text-sm text-slate-500">技能点级粒度 · 可按技术栈与置信度切换视图</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={cat} onChange={setCat} options={cats} className="w-40"
            icon={<Filter className="w-4 h-4" />} align="right" />
          <Select value={level} onChange={setLevel}
            options={levels.map(l => ({ value: l, label: l === '全部' ? '全部级别' : l }))}
            className="w-32" align="right" />
          <div className="rounded-xl bg-white/80 border border-slate-200 px-3.5 py-2.5 flex items-center gap-2 text-sm text-slate-600">
            置信≥{Math.round(minConf * 100)}%
            <input type="range" min={0} max={0.9} step={0.05} value={minConf}
              onChange={e => setMinConf(parseFloat(e.target.value))} className="accent-accent w-24" />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-5">
        <Card className="xl:col-span-3 p-2 relative overflow-hidden" hover={false}>
          {/* 图谱画布背景：柔和蓝白网络底图，避免单调 */}
          <div className="absolute inset-0 rounded-2xl pointer-events-none"
            style={{ backgroundImage: 'url(/graph-bg.png)', backgroundSize: 'cover', backgroundPosition: 'center' }} />
          <div className="absolute inset-0 rounded-2xl bg-white/25 pointer-events-none" />
          <div className="relative z-10">
            {loading ? <Spinner label="构建图谱中…" /> : (
              <ReactECharts ref={chartRef} option={option} style={{ height: 620 }} onEvents={onEvents}
                notMerge lazyUpdate />
            )}
          </div>
          {data && (
            <div className="absolute top-4 left-4 z-20 flex gap-2 text-xs">
              <Badge tone="indigo">岗位 {data.stats.jobs}</Badge>
              <Badge tone="cyan">技能点 {data.stats.skills}</Badge>
              <Badge tone="slate">关系 {data.stats.relations}</Badge>
            </div>
          )}
        </Card>

        <Card className="p-5">
          {sel ? <NodePanel node={sel} /> : (
            <div className="text-slate-500 text-sm flex flex-col items-center justify-center h-full text-center gap-3 py-10">
              <Maximize2 className="w-7 h-7 text-slate-600" />
              点击图谱中的<span className="text-slate-700 font-semibold">岗位/技能</span>节点查看详情<br />
              可拖拽、缩放、聚焦邻接关系
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function NodePanel({ node }: { node: any }) {
  const [detail, setDetail] = useState<any>(null)
  useEffect(() => {
    setDetail(null)
    if (node.type === 'skill') {
      const id = parseInt(String(node.id).replace('skill-', ''))
      api.skillDetail(id).then(setDetail)
    } else {
      const id = parseInt(String(node.id).replace('job-', ''))
      api.job(id).then(setDetail)
    }
  }, [node.id])

  if (node.type === 'skill') {
    return (
      <div className="space-y-3">
        <Badge tone="cyan">技能点</Badge>
        <div className="text-xl font-extrabold text-slate-900">{node.name}</div>
        <div className="text-xs text-slate-500">所属技术栈：{node.category}</div>
        <div className="label mt-3">关联岗位</div>
        <div className="space-y-1.5 max-h-72 overflow-auto">
          {detail?.related_jobs?.map((j: any) => (
            <div key={j.job_id} className="flex justify-between text-sm bg-sky-50/70 rounded-lg px-2.5 py-1.5">
              <span className="text-slate-700">{j.name}</span>
              <Badge tone={j.importance === 'required' ? 'indigo' : 'slate'}>
                {j.importance === 'required' ? '必备' : '加分'}
              </Badge>
            </div>
          )) || <div className="text-xs text-slate-400">加载中…</div>}
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge tone="indigo">岗位</Badge>{node.is_new && <Badge tone="amber">新兴</Badge>}
      </div>
      <div className="text-xl font-extrabold text-slate-900">{node.name}</div>
      {detail && (
        <>
          <p className="text-xs text-slate-500 leading-relaxed line-clamp-3">{detail.summary}</p>
          <div className="label mt-2">必备技能</div>
          <div className="flex flex-wrap gap-1.5">
            {detail.required_skills?.slice(0, 12).map((s: any) => (
              <Badge key={s.skill_id} tone="indigo">{s.name}</Badge>
            ))}
          </div>
          <a href={`/jobs/${detail.id}`} className="btn-primary w-full mt-3">查看完整画像</a>
        </>
      )}
    </div>
  )
}
