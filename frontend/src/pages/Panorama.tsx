import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Filter, GitBranch, Maximize2, Minus, Plus, Target } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { ITreeStructure } from '../components/icons'
import { api, GraphData, CATEGORY_COLORS } from '../api'
import { Card, Spinner, Badge, ErrorState } from '../components/ui'
import Select from '../components/Select'
import { useMouseParallax } from '../hooks/gsapFx'

export default function Panorama() {
  const [data, setData] = useState<GraphData | null>(null)
  const [cats, setCats] = useState<string[]>([])
  const [levels, setLevels] = useState<string[]>([])
  const [cat, setCat] = useState('全部')
  const [level, setLevel] = useState('全部')
  const [minConf, setMinConf] = useState(0)
  const [mode, setMode] = useState<'job' | 'capability' | 'skill'>('skill')
  const [recruitmentType, setRecruitmentType] = useState('全部')
  const [sel, setSel] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const chartRef = useRef<any>(null)
  const location = useLocation() as any
  const parallaxRef = useMouseParallax('.graph-bg-layer', { strength: 18 })

  useEffect(() => {
    api.categories().then(d => { setCats(['全部', ...d.categories]); setLevels(['全部', ...d.levels]) }).catch(() => {})
  }, [])
  const load = () => {
    setLoading(true); setError(false)
    api.panorama(cat, level, minConf, mode, undefined, recruitmentType === '全部' ? undefined : recruitmentType).then(d => {
      setData(d)
      if (location.state?.jobId) setSel(d.nodes.find((node: any) => String(node.id) === `job-${location.state.jobId}`) || null)
      setLoading(false)
    })
      .catch(() => { setError(true); setLoading(false) })
  }
  useEffect(load, [cat, level, minConf, mode, recruitmentType])

  const option = useMemo(() => {
    if (!data) return {}
    // 窄屏下缩小初始视图并简化标签，避免节点文字被画布左右边缘裁切
    const isNarrow = typeof window !== 'undefined' && window.innerWidth < 640
    const hex2rgba = (hex: string, a: number) => {
      const m = hex.replace('#', '')
      const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16)
      return `rgba(${r},${g},${b},${a})`
    }
    // 浅色渐变调色板（每个技术栈一对 [浅, 深]）。
    // 深端必须与 api.ts 的 CATEGORY_COLORS 逐项一致——那张表管卡片圆点和图例，
    // 这张表管力导图节点；两处对不上，同一个岗位在列表页和图谱里就是两种颜色。
    // 全表不含绿 / 青绿：节点 hover 时 emphasis 会把本色提亮，原先的
    // 物联网 #10B981（emerald）和 数据库与存储 #14B8A6（teal）正是"悬浮发绿"的来源。
    const CAT_GRAD: Record<string, [string, string]> = {
      人工智能: ['#93C5FD', '#3B82F6'],
      大数据: ['#7DD3FC', '#0EA5E9'],
      物联网: ['#B6AEEA', '#7A6BD8'],
      智能系统: ['#FCD34D', '#F59E0B'],
      云计算与工程: ['#D8B4FE', '#A855F7'],
      数据工程: ['#F9A8D4', '#F472B6'],
      // 技能侧独有的两类（岗位不会用到）：拆自原先什么都往里塞的「云计算与工程」
      编程语言: ['#BAE6FD', '#38BDF8'],
      数据库与存储: ['#A5B4FC', '#4F46E5'],
      其他: ['#CBD5E1', '#64748B'],
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
      const isCapability = n.type === 'capability' || n.type === 'cluster'
      const [light, dark] = CAT_GRAD[n.category] || CAT_GRAD['其他']
      return {
        id: n.id, name: n.name,
        symbolSize: isJob ? Math.max(38, Math.min(58, n.value + 10)) : isCapability ? Math.max(25, Math.min(40, n.value + 4)) : Math.max(13, Math.min(26, n.value - 1)),
        category: isJob ? 0 : isCapability ? 1 : 2,
        itemStyle: isJob
          ? {
              // 岗位：扁平硬币 + 通透白描边 + 同色柔光晕
              color: linearV(mixWhite(dark, 0.30), dark),
              borderColor: n.is_new ? '#F59E0B' : 'rgba(255,255,255,0.92)',
              borderWidth: n.is_new ? 2.5 : 2,
              shadowBlur: 26, shadowColor: hex2rgba(dark, 0.33),
            }
          : isCapability ? {
              color: linearV('#FFFFFF', mixWhite(dark, 0.5)), borderColor: dark, borderWidth: 1.5,
              shadowBlur: 12, shadowColor: hex2rgba(dark, 0.18),
            } : {
              // 技能：极简扁平半透明圆点，airy 不喧宾夺主
              color: mixWhite(light, 0.40),
              opacity: 0.92,
              borderColor: '#ffffff', borderWidth: 1.5,
              shadowBlur: 9, shadowColor: hex2rgba(light, 0.4),
            },
        label: {
          show: isJob || isCapability || !isNarrow,
          position: isJob ? 'bottom' : 'right',
          distance: isJob ? 8 : 6,
          color: isJob ? '#0F172A' : '#7C8BA3',
          fontSize: isJob ? (isNarrow ? 10 : 11.5) : 10,
          fontWeight: isJob ? 700 : 500,
          width: isNarrow ? 96 : undefined,
          overflow: isNarrow ? 'truncate' : undefined,
          // 岗位标签给白底 + 淡描边：力导向下标签难免有贴近的时候，描边让上下两层
          // 各自成块，即使贴住也还能一眼分开是两个岗位。
          backgroundColor: isJob ? 'rgba(255,255,255,0.92)' : 'transparent',
          borderColor: isJob ? 'rgba(148,163,184,0.35)' : 'transparent',
          borderWidth: isJob ? 1 : 0,
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
        // repulsion 给数组：ECharts 按节点 value 在区间内插值，岗位节点（value=30，
        // 高于绝大多数技能点的 degree）因此获得最大斥力。此前用单值 560，少边的岗位
        // （数字孪生、自动驾驶等语料偏薄的岗位）会被 gravity 拽到画布中心叠在一起，
        // 名字长的标签糊成一团——hideOverlap 只能把它们藏掉，藏掉又等于信息丢失，
        // 所以要从布局上把节点先分开。
        force: isNarrow
          ? { repulsion: [160, 420], edgeLength: [70, 150], gravity: 0.16, friction: 0.18 }
          : { repulsion: [320, 1000], edgeLength: [140, 300], gravity: 0.092, friction: 0.16 },
        categories: [{ name: '岗位' }, { name: '能力簇' }, { name: '技能点' }],
        // hideOverlap 单用不够：力导向布局下岗位标签仍会两两压住（岗位标签带白底
        // 色块，压在一起时下层的名字直接读不出来）。moveOverlap 先把冲突的标签沿 Y
        // 轴错开，实在错不开的再交给 hideOverlap 隐藏——优先保住"能读"，其次才是"都显示"。
        labelLayout: { hideOverlap: true, moveOverlap: 'shiftY' },
        // 平滑状态切换 + 仅节点触发柔和聚焦，杜绝鼠标划过连线时的"屏闪"
        stateAnimation: { duration: 280, easing: 'cubicOut' },
        emphasis: { focus: 'adjacency',
          label: { show: true }, lineStyle: { width: 2, color: 'rgba(129,140,248,0.6)' } },
        blur: { itemStyle: { opacity: 0.65 }, lineStyle: { opacity: 0.08 }, label: { opacity: 0.45 } },
        data: nodes, links, scaleLimit: { min: 0.3, max: 4 }, center: ['50%', '50%'], zoom: isNarrow ? 0.6 : 0.78,
      }],
    }
  }, [data])

  const onEvents = {
    click: (p: any) => { if (p.dataType === 'node') setSel(p.data._raw) },
  }

  /* 触屏被 touch-action:pan-y 挡掉了双指缩放，这里用 graphRoam 把缩放能力补回来。
     用 try/catch 包住：实例还没挂载或 action 不可用时静默跳过，不影响图谱本身。 */
  const zoomBy = (factor: number) => {
    try {
      const inst = chartRef.current?.getEchartsInstance?.()
      if (!inst) return
      const box = inst.getDom()?.getBoundingClientRect?.()
      inst.dispatchAction({
        type: 'graphRoam', seriesIndex: 0, zoom: factor,
        originX: box ? box.width / 2 : undefined, originY: box ? box.height / 2 : undefined,
      })
    } catch { /* 缩放是增强项，失败不打断浏览 */ }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 shrink-0 rounded-xl bg-grad-accent ring-1 ring-accent/20 grid place-items-center shadow-glow">
            <ITreeStructure className="w-6 h-6 text-accent-deep" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold text-slate-900">新一代信息技术岗位全景图谱</h1>
            <p className="text-sm text-slate-500">技能点级粒度 · 可按技术栈与置信度切换视图</p>
          </div>
        </div>
        {/* 窄屏：2 列网格，控件铺满栅格；sm 起切回原来的定宽 flex 排布。
            纯断点驱动，不做 JS 宽度判断。 */}
        <div className="grid w-full grid-cols-2 items-center gap-2 sm:flex sm:w-auto sm:flex-wrap">
          <div className="col-span-2 flex rounded-xl border border-slate-200 bg-white/80 p-1" aria-label="图谱视图层级">
            {([['job', '岗位'], ['capability', '能力簇'], ['skill', '技能点']] as const).map(([value, label]) => (
              <button key={value} onClick={() => setMode(value)} className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-semibold transition sm:flex-none ${mode === value ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>{label}</button>
            ))}
          </div>
          <Select value={cat} onChange={setCat} options={cats} className="w-full sm:w-40"
            icon={<Filter className="w-4 h-4" />} align="right" />
          <Select value={level} onChange={setLevel}
            options={levels.map(l => ({ value: l, label: l === '全部' ? '全部级别' : l }))}
            className="w-full sm:w-32" align="right" />
          <Select value={recruitmentType} onChange={setRecruitmentType} className="col-span-2 w-full sm:w-28" align="right" label="招聘类型" options={[
            { value: '全部', label: '校招/社招' }, { value: 'campus', label: '校招' }, { value: 'social', label: '社招' }, { value: 'mixed', label: '混合' },
          ]} />
          <div className="col-span-2 flex items-center gap-2 rounded-xl border border-slate-200 bg-white/80 px-3.5 py-2.5 text-sm text-slate-600 sm:col-span-1">
            <span className="whitespace-nowrap">置信≥{Math.round(minConf * 100)}%</span>
            <input type="range" aria-label="最低置信度" min={0} max={0.9} step={0.05} value={minConf}
              onChange={e => setMinConf(parseFloat(e.target.value))} className="accent-accent w-full sm:w-24" />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-5 xl:grid-cols-4">
        <div ref={parallaxRef} className="lg:col-span-2 xl:col-span-3">
        <Card className="p-2 relative overflow-hidden" hover={false}>
          <div className="absolute inset-0 rounded-2xl overflow-hidden">
            {/* 图谱画布背景：柔和蓝白网络底图 + 鼠标轻微视差（scale 预留位移余量） */}
            <div className="graph-bg-layer absolute -inset-4 pointer-events-none will-change-transform"
              style={{ backgroundImage: 'url(/graph-bg2.webp)', backgroundSize: 'cover', backgroundPosition: 'center' }} />
            <div className="absolute inset-0 bg-white/25 pointer-events-none" />
          </div>
          {/* touch-pan-y：竖向手势交还给浏览器（页面照常滚动），横向拖拽/点击节点
              仍进入 ECharts。触屏被 pan-y 挡掉的双指缩放，用画布右下角的按钮补回。
              touch-action 只作用于触摸输入，桌面滚轮缩放与拖拽完全不受影响。 */}
          <div className="relative z-10">
            {loading ? <Spinner label="构建图谱中…" /> : error ? <ErrorState text="图谱加载失败" onRetry={load} /> : (
              <div className="relative h-[360px] touch-pan-y sm:h-[520px] lg:h-[560px] xl:h-[620px]">
                <ReactECharts ref={chartRef} option={option} style={{ height: '100%' }} onEvents={onEvents}
                  notMerge lazyUpdate />
                <div className="absolute bottom-2 right-2 z-20 flex flex-col gap-1.5 rounded-xl border border-slate-200 bg-white/85 p-1 shadow-sm backdrop-blur">
                  <button onClick={() => zoomBy(1.3)} aria-label="放大图谱"
                    className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition hover:bg-slate-100 active:scale-95"><Plus className="h-4 w-4" /></button>
                  <button onClick={() => zoomBy(1 / 1.3)} aria-label="缩小图谱"
                    className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition hover:bg-slate-100 active:scale-95"><Minus className="h-4 w-4" /></button>
                </div>
              </div>
            )}
          </div>
          {data && (
            <div className="absolute top-3 left-3 right-3 z-20 flex flex-wrap gap-1.5 text-xs sm:top-4 sm:left-4 sm:right-auto sm:gap-2">
              <Badge tone="indigo">岗位 {data.stats.jobs}</Badge>
              <Badge tone="cyan">技能点 {data.stats.skills}</Badge>
              {data.stats.capabilities != null && <Badge tone="amber">能力簇 {data.stats.capabilities}</Badge>}
              <Badge tone="slate">关系 {data.stats.relations}</Badge>
            </div>
          )}
        </Card>
        </div>

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

  if (node.type === 'capability' || node.type === 'cluster') {
    return (
      <div className="space-y-3"><Badge tone="amber">能力簇</Badge><div className="text-xl font-extrabold text-slate-900">{node.name}</div><div className="text-xs text-slate-500">所属领域：{node.category || '通用'}</div>{node.skills?.length > 0 && <><div className="label mt-3">细分技能点</div><div className="flex flex-wrap gap-1.5">{node.skills.map((skill: any) => <Badge key={typeof skill === 'string' ? skill : skill.name} tone="slate">{typeof skill === 'string' ? skill : skill.name}</Badge>)}</div></>}</div>
    )
  }
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
          <div className="grid grid-cols-1 gap-2 mt-3"><Link to={`/jobs/${detail.id}`} className="btn-primary w-full">查看完整画像</Link><Link to="/match" state={{ jobId: detail.id }} className="btn-ghost w-full"><Target className="w-4 h-4" /> 人岗匹配</Link><Link to="/evolution" state={{ jobId: detail.id }} className="btn-ghost w-full"><GitBranch className="w-4 h-4" /> 查看演化</Link></div>
        </>
      )}
    </div>
  )
}
