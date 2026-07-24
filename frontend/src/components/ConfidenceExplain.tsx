import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ConfidenceFactors } from '../api'

/** 置信度计算公式（与后端 hallucination.aggregate_capabilities 保持一致） */
export const FACTOR_DEFS: { key: keyof ConfidenceFactors; label: string; weight: number }[] = [
  { key: 'support', label: '支持率', weight: 0.35 },
  { key: 'diversity', label: '来源多样性', weight: 0.20 },
  { key: 'freshness', label: '时效性', weight: 0.15 },
  { key: 'authority', label: '来源权威度', weight: 0.20 },
  { key: 'external', label: '外部验证', weight: 0.10 },
]
export const FORMULA_TEXT = 'C = 0.35×支持率 + 0.20×来源多样性 + 0.15×时效性 + 0.20×来源权威度 + 0.10×外部验证'

const POP_W = 300

/**
 * 置信度解释弹层：点击置信度徽章展开五因子分解条形图 + 公式。
 * 通过 Portal 渲染到 body，位置基于触发元素并夹在视口内；外部点击/滚动关闭。
 */
export default function ConfidenceExplain({ value, factors, children }: {
  value: number; factors: ConfidenceFactors; children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const place = () => {
    const r = btnRef.current?.getBoundingClientRect()
    if (!r) return
    const left = Math.max(8, Math.min(r.left + r.width / 2 - POP_W / 2, window.innerWidth - POP_W - 8))
    // 默认展示在下方；若下方空间不足则翻到上方（弹层约 260px 高）
    const below = r.bottom + 8
    const top = below + 260 > window.innerHeight && r.top > 280 ? r.top - 268 : below
    setPos({ top, left })
  }

  useEffect(() => {
    if (!open) return
    place()
    const onDoc = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || popRef.current?.contains(t)) return
      setOpen(false)
    }
    const onScroll = () => setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('touchstart', onDoc)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('touchstart', onDoc)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
  }, [open])

  const total = FACTOR_DEFS.reduce((s, f) => s + f.weight * (factors[f.key] ?? 0), 0)

  return (
    <>
      <button ref={btnRef} type="button" aria-label="查看置信度计算说明"
        onClick={e => { e.stopPropagation(); e.preventDefault(); setOpen(o => !o) }}
        className="inline-flex cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-full">
        {children}
      </button>
      {open && pos && createPortal(
        <div ref={popRef} role="dialog" aria-label="置信度计算说明"
          className="fixed z-[80] rounded-xl border border-slate-200 bg-white shadow-xl p-4"
          style={{ top: pos.top, left: pos.left, width: POP_W }}>
          <div className="text-xs font-bold text-slate-700 mb-2 flex items-center justify-between">
            <span>置信度是怎么算的？</span>
            <span className="text-accent font-extrabold">{Math.round(value * 100)}%</span>
          </div>
          <div className="space-y-2">
            {FACTOR_DEFS.map(f => {
              const v = factors[f.key] ?? 0
              const contrib = f.weight * v
              return (
                <div key={f.key}>
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span>{f.label} <span className="text-slate-400">×{f.weight.toFixed(2)}</span></span>
                    <span className="tabular-nums">{v.toFixed(2)} → 贡献 {(contrib * 100).toFixed(1)}%</span>
                  </div>
                  <div className="h-1.5 mt-0.5 rounded-full bg-slate-100 overflow-hidden">
                    <div className="h-full rounded-full bg-grad-accent" style={{ width: `${Math.round(v * 100)}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
          <div className="mt-3 pt-2 border-t border-slate-100 text-[10px] leading-relaxed text-slate-400">
            {FORMULA_TEXT}
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            合计 <span className="font-bold text-slate-700 tabular-nums">{(total * 100).toFixed(1)}%</span>
            （多源交叉验证，越高越可信）
          </div>
        </div>, document.body)}
    </>
  )
}
