import { useCallback, useEffect, useRef, useState, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Check } from 'lucide-react'

type Opt = string | { value: string; label: string }

function norm(o: Opt) { return typeof o === 'string' ? { value: o, label: o } : o }

/** 触发器与弹层之间的间隙（原先靠 mt-2，改 fixed 后由 JS 给出） */
const GAP = 8
/** 弹层距视口边缘的安全内缩，与 ConfidenceExplain 保持一致 */
const MARGIN = 8
/** 弹层最大高度：256（列表 max-h-64）+ 12（p-1.5 上下） */
const POP_MAX = 268
/** 下方可用空间小于此值时考虑上翻 */
const FLIP_AT = 240

type Pos = { top?: number; bottom?: number; left: number; width: number; maxH: number; flip: boolean }

export default function Select({ value, onChange, options, placeholder = '请选择', className = '', icon, align = 'left', label }: {
  value: string | number | null
  onChange: (v: string) => void
  options: Opt[]
  placeholder?: string
  className?: string
  icon?: ReactNode
  align?: 'left' | 'right'
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1) // 键盘高亮项
  const [pos, setPos] = useState<Pos | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const opts = options.map(norm)
  const current = opts.find(o => String(o.value) === String(value))

  /* 弹层改用 Portal + fixed：原先是 absolute z-30，而祖先 Card 是带 transform 的
     motion.div，会创建层叠上下文，z-30 逃不出去，会被 DOM 顺序更靠后的兄弟卡片盖住。
     参照 components/ConfidenceExplain.tsx 的既有做法。 */
  const place = useCallback(() => {
    const b = btnRef.current
    if (!b) return
    const r = b.getBoundingClientRect()
    // 触发器已滚出视口 → 关闭，避免菜单孤悬在页面上
    if (r.bottom < 0 || r.top > window.innerHeight) { setOpen(false); return }

    const below = window.innerHeight - r.bottom - GAP - MARGIN
    const above = r.top - GAP - MARGIN
    const flip = below < FLIP_AT && above > below
    const maxH = Math.max(140, Math.min(POP_MAX, flip ? above : below))
    const width = r.width // 宽度锁定为触发器宽度（原 min-w-full 会让长选项把弹层撑得比触发器还宽、向右溢出）
    // align 语义保留：右对齐以触发器右边缘为基准（width === r.width 时两者等价）
    const rawLeft = align === 'right' ? r.right - width : r.left
    const left = Math.max(MARGIN, Math.min(rawLeft, window.innerWidth - width - MARGIN))

    // 上翻用 bottom 而非 top 锚定：弹层实际高度通常小于 maxH，
    // 用 top 会在选项少时于菜单与触发器之间裂出空隙
    setPos(flip
      ? { bottom: window.innerHeight - r.top + GAP, left, width, maxH, flip }
      : { top: r.bottom + GAP, left, width, maxH, flip })
  }, [align])

  // 先测量再开，两次 setState 合批为一次渲染，避免"先渲染后测量"的一帧闪烁
  const openMenu = () => { place(); setOpen(true) }

  // 点击外部关闭。Portal 后弹层已不是 ref 的后代，必须同时检查 popRef，
  // 否则点击选项时菜单会先卸载、click 永不触发，下拉框变成只读
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || popRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', h)
    document.addEventListener('touchstart', h)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('touchstart', h)
    }
  }, [open])

  /* 滚动/缩放时重新定位，而不是关闭。捕获阶段的 scroll 监听会捕捉到任意后代滚动容器，
     包括本组件自己 overflow-y-auto 的选项列表——若照 ConfidenceExplain 那样 close-on-scroll，
     用户滚动长列表（如 100 项岗位）时菜单会立刻关掉。 */
  useEffect(() => {
    if (!open) return
    place()
    let raf = 0
    const onMove = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(place) }
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open, place, opts.length])

  /* 滚动到指定项。不用 scrollIntoView：它会遍历所有可滚动祖先，
     fixed 弹层下可能连带滚动页面，进而触发重定位、产生跳动 */
  const scrollToIdx = (i: number) => {
    const box = listRef.current
    const el = box?.children[i] as HTMLElement | undefined
    if (!box || !el) return
    const top = el.offsetTop
    const bot = top + el.offsetHeight
    if (top < box.scrollTop) box.scrollTop = top
    else if (bot > box.scrollTop + box.clientHeight) box.scrollTop = bot - box.clientHeight
  }

  // 打开时高亮当前项并滚动到可见
  useEffect(() => {
    if (open) {
      const idx = opts.findIndex(o => String(o.value) === String(value))
      setHi(idx >= 0 ? idx : 0)
      requestAnimationFrame(() => scrollToIdx(idx >= 0 ? idx : 0))
    }
  }, [open])

  const commit = (v: string) => { onChange(v); setOpen(false); btnRef.current?.focus() }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); openMenu() }
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false); btnRef.current?.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setHi(i => Math.min(opts.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setHi(0) }
    else if (e.key === 'End') { e.preventDefault(); setHi(opts.length - 1) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (hi >= 0) commit(opts[hi].value) }
    else if (e.key === 'Tab') setOpen(false)
  }

  useEffect(() => {
    if (open && hi >= 0) scrollToIdx(hi)
  }, [hi, open])

  return (
    <div ref={ref} className={`relative ${className}`} onKeyDown={onKeyDown}>
      <button ref={btnRef} type="button" onClick={() => (open ? setOpen(false) : openMenu())}
        aria-haspopup="listbox" aria-expanded={open} aria-label={label || placeholder}
        className={`w-full flex items-center gap-2 rounded-xl bg-white/80 border px-3.5 py-2.5 text-sm text-slate-700 transition outline-none
          focus-visible:ring-2 focus-visible:ring-accent/30
          ${open ? 'border-accent/60 ring-2 ring-accent/15 bg-white' : 'border-slate-200 hover:border-sky-300'}`}>
        {icon && <span className="text-slate-400 shrink-0">{icon}</span>}
        <span className={`flex-1 text-left truncate ${current ? '' : 'text-slate-400'}`}>{current?.label ?? placeholder}</span>
        <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {/* AnimatePresence 必须在 portal 内部：framer-motion 用 isValidElement 过滤子节点，
          portal 的 $$typeof 是 REACT_PORTAL_TYPE，套在外面会被静默丢弃、菜单永不渲染。
          z-[70] 落在既有梯度内：Toast 100 > ConfirmDialog 90 > ConfidenceExplain 80 > 本组件 70 > ChatBot 50。
          注意：pos 在关闭时不清空，退场动画需要它存活。 */}
      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div key="menu" ref={popRef} onKeyDown={onKeyDown}
              initial={{ opacity: 0, y: pos.flip ? 6 : -6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: pos.flip ? 6 : -6, scale: 0.98 }} transition={{ duration: 0.16 }}
              className="fixed z-[70] rounded-2xl bg-white border border-slate-200 shadow-xl overflow-hidden"
              style={{
                top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.width,
                boxShadow: '0 16px 40px -12px rgba(37,99,235,0.25)',
              }}>
              {/* 内层滚动：外层 overflow-hidden 裁掉圆角，滚动条不再穿出容器。
                  relative 是为了让 scrollToIdx 的 offsetTop 相对列表测量 */}
              <div ref={listRef} role="listbox" aria-label={label || placeholder}
                className="relative overflow-y-auto p-1.5" style={{ maxHeight: pos.maxH - 12 }}>
                {opts.map((o, i) => {
                  const active = String(o.value) === String(value)
                  return (
                    <button key={o.value} type="button" role="option" aria-selected={active} data-active={active}
                      onClick={() => commit(o.value)} onMouseEnter={() => setHi(i)} tabIndex={-1}
                      title={o.label}
                      className={`w-full flex items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm text-left transition
                        ${active ? 'bg-grad-accent text-white font-semibold' : hi === i ? 'bg-sky-50 text-slate-700' : 'text-slate-600 hover:bg-sky-50'}`}>
                      <span className="truncate">{o.label}</span>
                      {active && <Check className="w-4 h-4 shrink-0" />}
                    </button>
                  )
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body)}
    </div>
  )
}
